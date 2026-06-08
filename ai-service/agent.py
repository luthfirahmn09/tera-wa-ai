"""Otak bot.

Alur:
  1. LLM Groq klasifikasi pesan user -> action + parameter (intent).
  2. Router jalankan aksi: cari produk, kelola keranjang, checkout, bantuan.

Balasan untuk pencarian disusun LLM (ramah). Balasan keranjang/checkout
pakai template (deterministik & anti-halusinasi).
"""
from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

import memory
from cart import (
    add_to_cart,
    clear_cart,
    get_cart,
    get_last_list,
    next_search_page,
    remove_from_cart,
    reset_search,
    set_last_list,
    set_search,
)
from catalog import all_products, find_by_name, get_categories, get_product
from config import (
    GROQ_FALLBACK_MODELS,
    GROQ_MODEL,
    MAX_INPUT_CHARS,
    MAX_OUTPUT_CHARS,
    PAGE_SIZE,
    SEARCH_LIMIT,
    STORE_NAME,
    get_groq_api_key,
)
from search import has_keywords, list_all, search_by_price, search_ranked

HELP_TEXT = (
    f"*Cara order di {STORE_NAME}:* 🛍️\n"
    "1️⃣ Cari produk → contoh: *cari gamis harga 50rb*\n"
    "2️⃣ Simpan ke keranjang → *simpan no 1* atau *masukkan keranjang gamis katun*\n"
    "3️⃣ Lihat keranjang → *keranjang saya*\n"
    "4️⃣ Hapus item → *hapus no 1* / *hapus gamis* / *kosongkan keranjang*\n"
    "5️⃣ Bayar → ketik *bayar* atau *checkout*\n\n"
    "Kamu bisa langsung *bayar* setelah cari, atau simpan dulu di keranjang "
    "baru bayar. 😊"
)


# ---------- intent ----------

class Intent(BaseModel):
    action: Literal[
        "search",            # cari produk
        "add_to_cart",       # simpan ke keranjang
        "view_cart",         # lihat keranjang
        "remove_from_cart",  # hapus item / kosongkan keranjang
        "checkout",          # bayar
        "more",              # tampilkan lagi hasil pencarian (lanjut/ada lagi)
        "product_detail",    # tanya detail produk (ukuran/warna/bahan/stok/ready)
        "help",              # cara order
        "greeting",          # sapaan (halo, hai, pagi)
        "thanks",            # ucapan terima kasih (makasih, thanks)
        "chitchat",          # obrolan santai/reaksi (mantap, ok, sip, keren, wkwk)
        "gibberish",         # teks acak/tdk bermakna (mis. 'asdfgh', kepencet)
        "inappropriate",     # kasar/jorok/seksual/18+/SARA/ujaran kebencian
        "out_of_scope",      # pertanyaan/permintaan di luar belanja pakaian
    ] = Field(description="Maksud utama pesan user.")

    # untuk action=search
    query: Optional[str] = Field(
        default=None, description="Inti pencarian, mis. 'gamis adem longgar'."
    )
    kategori: Optional[str] = Field(
        default=None,
        description="Segmen produk, HANYA salah satu kategori yang tersedia di "
        "toko (lihat daftar di sistem). Isi hanya bila user jelas menyebut "
        "segmennya. Untuk jenis barang (mis. 'kemeja', 'jaket') biarkan null "
        "dan andalkan pencarian makna.",
    )
    max_harga: Optional[int] = Field(
        default=None,
        description="Batas harga maksimal dalam rupiah angka penuh. "
        "'50rb'->50000, '1jt'->1000000. null jika tidak disebut.",
    )
    sort: Optional[Literal["price_asc", "price_desc"]] = Field(
        default=None,
        description="Urutan harga bila diminta: 'termurah/paling murah' -> "
        "price_asc; 'termahal/paling mahal' -> price_desc. null jika tidak.",
    )

    # untuk action=add_to_cart / remove_from_cart
    product_ref: Optional[str] = Field(
        default=None,
        description="Rujukan produk: bisa NOMOR dari daftar terakhir (mis. '2') "
        "atau nama produk (mis. 'gamis katun'). Untuk 'kosongkan keranjang' "
        "isi 'semua'.",
    )


# Cache per-(key, model): begitu key di Redis berubah, get_groq_api_key()
# mengembalikan string baru -> entri cache baru -> client otomatis dibangun
# ulang tanpa restart. Per-model supaya fallback tak bikin client baru tiap call.
@lru_cache(maxsize=16)
def _llm_for(api_key: str, model: str) -> ChatGroq:
    return ChatGroq(api_key=api_key, model=model, temperature=0)


def _llm(model: str | None = None) -> ChatGroq:
    return _llm_for(get_groq_api_key(), model or GROQ_MODEL)


# urutan model yang dicoba: utama dulu, lalu cadangan (dedup, jaga urutan)
def _model_chain() -> list[str]:
    out, seen = [], set()
    for m in [GROQ_MODEL, *GROQ_FALLBACK_MODELS]:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _should_fallback(err: Exception) -> bool:
    """Layak coba model lain? rate limit ATAU model gagal tool/structured-output
    (model kecil sering salah skema)."""
    s = str(err).lower()
    return any(
        k in s
        for k in (
            "rate_limit", "429", "tokens per day",  # kuota
            "tool_use_failed", "did not match schema",
            "tool call validation", "failed to call",  # tool/schema
        )
    )


def _invoke_llm(build_chain, inputs: dict):
    """Jalankan chain dengan fallback: kalau model kena rate limit ATAU gagal
    menghasilkan structured output, coba model berikutnya. build_chain(llm)
    -> Runnable.
    """
    last_err: Exception | None = None
    for model in _model_chain():
        try:
            return build_chain(_llm(model)).invoke(inputs)
        except Exception as err:  # noqa: BLE001
            if _should_fallback(err):
                last_err = err
                print(f"[llm] '{model}' gagal ({str(err)[:60]}…), coba model berikutnya…")
                continue
            raise
    raise last_err if last_err else RuntimeError("tidak ada model LLM tersedia")


_INTENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            f"Kamu asisten {STORE_NAME}, toko pakaian, via WhatsApp. Klasifikasi "
            "pesan user ke salah satu action: search, add_to_cart, view_cart, "
            "remove_from_cart, checkout, more, product_detail, help, greeting, "
            "thanks, chitchat, gibberish, out_of_scope.\n"
            "- 'cari/ada/punya <produk>' -> search\n"
            "- 'simpan/masukkan keranjang/tambah' -> add_to_cart\n"
            "- 'beli semua/ambil semua/semuanya mau dibeli/mau semua/semuanya' "
            "-> add_to_cart dgn product_ref='semua' (masukin semua yg tampil). "
            "INI BUKAN checkout.\n"
            "- 'keranjang saya/lihat keranjang/cek keranjang' -> view_cart\n"
            "- 'hapus/buang/kosongkan keranjang' -> remove_from_cart\n"
            "- 'bayar/checkout/order/pesan sekarang' -> checkout\n"
            "- 'lanjut/lanjutkan/ada lagi/yang lain/lihat lagi/next/lainnya' "
            "(minta lihat lebih banyak hasil pencarian sebelumnya) -> more\n"
            "- tanya DETAIL/atribut produk (ukuran/size, warna, bahan/material, "
            "stok, ready/ada nggak, berat, dimensi, garansi) -> product_detail\n"
            "- 'cara order/gimana caranya/help/bantuan' -> help\n"
            "- sapaan (halo, hai, pagi) -> greeting\n"
            "- ucapan terima kasih (makasih, thanks, suwun) -> thanks\n"
            "- reaksi/obrolan santai (mantap, oke, sip, keren, wkwk, gas, "
            "mantul, bagus) -> chitchat\n"
            "- teks acak/tidak bermakna/typo ngawur (mis 'asdfgh', 'dawdwad', "
            "'kkkk', cuma simbol) -> gibberish\n"
            "- kata kasar/jorok/vulgar, konten seksual/18+, ujaran kebencian "
            "atau SARA (hinaan suku/agama/ras), pelecehan -> inappropriate\n"
            "- user MENGIYAKAN / minta ditunjukkan produk (mis 'boleh', 'mau', "
            "'iya', 'boleh deh', 'apa yang mantap', 'ada rekomendasi', 'liatin "
            "dong') -> search. Kalau user sinyal pengen MURAH/hemat ('gaada "
            "duit', 'lagi bokek', 'yang murah') -> search + sort=price_asc. "
            "Gunakan history: kalau bot barusan nawarin lihat produk dan user "
            "setuju, itu search.\n"
            "- pertanyaan/permintaan yang BENAR-BENAR di luar belanja pakaian "
            "(pertanyaan umum, coding, politik, matematika, cuaca, gosip) -> "
            "out_of_scope\n"
            "PENTING: pertanyaan soal produk/toko (termasuk ukuran, warna, bahan, "
            "stok, pengiriman) TETAP in-scope — JANGAN jadikan out_of_scope.\n"
            "ATURAN penting bedakan search vs product_detail:\n"
            "  • Sebut JENIS/NAMA produk (baju, kaos, gamis, sepatu, tas, celana, "
            "hijab, dll), walau pakai 'apa aja' -> SEARCH.\n"
            "  • Sebut ATRIBUT produk (size/ukuran, warna, bahan, stok, ready, "
            "berat) tanpa jenis produk baru -> product_detail.\n"
            "Contoh: 'ada baju apa aja?'=search; 'cari kaos'=search; "
            "'ada gamis?'=search; 'ada size apa aja?'=product_detail; "
            "'warna apa aja?'=product_detail; 'bahannya apa?'=product_detail; "
            "'ada stok?'=product_detail; 'halo'=greeting; "
            "'siapa presiden?'=out_of_scope.\n"
            "Ubah harga: '50rb'->50000, '50 ribu'->50000, '1jt'->1000000. "
            "product_ref isi nomor (mis '2') atau nama produk bila ada.\n"
            "Kategori yang tersedia di toko: {categories}. kategori hanya boleh "
            "salah satu dari itu, dan hanya diisi kalau user jelas menyebut "
            "segmennya; selain itu null.\n"
            "Kata yang berarti MURAH (murah/termurah/paling murah/murce/murmer/"
            "hemat/cheap/low price/yang murah) -> sort=price_asc. Kata yang berarti "
            "MAHAL (mahal/termahal/paling mahal/expensive/yang mahal) -> "
            "sort=price_desc. Action tetap search. Kalau user tak menyebut jenis "
            "barang (cuma minta yang termurah/termahal), query boleh null.\n"
            "Pakai riwayat chat untuk memahami pesan lanjutan: 'yang lebih murah'/"
            "'yang 50rb aja' = lanjutan pencarian sebelumnya, jadi isi query & "
            "filter dari konteks itu. Tapi action selalu berdasarkan pesan TERBARU.",
        ),
        MessagesPlaceholder("history"),
        ("human", "{text}"),
    ]
)


def _to_messages(history: list[dict]) -> list:
    msgs = []
    for h in history:
        content = h.get("text", "")
        if h.get("role") == "user":
            msgs.append(HumanMessage(content=content))
        else:
            msgs.append(AIMessage(content=content))
    return msgs


def extract_intent(text: str, history: list[dict] | None = None) -> Intent:
    categories = ", ".join(get_categories()) or "(tidak ada)"
    return _invoke_llm(
        lambda llm: _INTENT_PROMPT | llm.with_structured_output(Intent),
        {
            "text": text,
            "history": _to_messages(history or []),
            "categories": categories,
        },
    )


# ---------- reply pencarian (template + multi-bubble, tanpa sapaan) ----------

_NOT_FOUND = (
    "Hmm, aku belum nemu yang pas nih 🙏 "
    "Coba kata kunci lain, atau budget-nya dinaikin dikit ya?"
)

# kalimat pembuka (bubble pertama) — divariasikan biar luwes & TANPA "Hai/Halo"
_LEAD_CHEAP = ["Tenang, ini yang ramah di kantong 👇", "Sip, ini yang murah-murah 👇", "Nih yang hemat 👇"]
_LEAD_PRICEY = ["Ini yang premium 👇", "Nih yang kelas atas 👇"]
_LEAD_NORMAL = ["Nih beberapa yang oke 👇", "Cus, lihat ini dulu 👇", "Ini yang aku punya buat kamu 👇"]
_LEAD_MORE = ["Lanjut ya, ini berikutnya 👇", "Sip, ini sisanya 👇", "Masih ada nih 👇"]


def _lead_in(intent: Optional[Intent], seed: str) -> str:
    if intent and intent.sort == "price_asc":
        return _pick(_LEAD_CHEAP, seed)
    if intent and intent.sort == "price_desc":
        return _pick(_LEAD_PRICEY, seed)
    return _pick(_LEAD_NORMAL, seed)


def _product_list_text(products: list[dict], remaining: int) -> str:
    lines = []
    for i, p in enumerate(products, 1):
        lines.append(f"{i}. *{p['nama']}* — Rp{p['harga']:,}\n{p['link']}")
    body = "\n\n".join(lines)
    body += (
        "\n\nKetik *simpan no <x>* buat masukin keranjang, "
        "atau *bayar* kalau udah sip. 🛍️"
    )
    if remaining > 0:
        body += f"\n\n📦 Masih ada {remaining} lagi — ketik *lanjut* ya 😊"
    return body


def _render_page(lead: str, batch_ids: list[str], remaining: int, user: str) -> list[str]:
    """Hasil pencarian = 2 bubble: kalimat pembuka + daftar produk (template)."""
    products = [p for p in (get_product(pid) for pid in batch_ids) if p]
    if not products:
        return [_NOT_FOUND]
    set_last_list(user, [p["id"] for p in products])  # biar 'simpan no x' pas
    return [lead, _product_list_text(products, remaining)]


def _do_search(text: str, intent: Intent, user: str) -> list[str]:
    # pakai query hasil ekstraksi saja; JANGAN fallback ke teks mentah
    # (teks obrolan spt "gaada duit" bukan kata kunci produk -> browse).
    query = (intent.query or "").strip()
    if intent.sort in ("price_asc", "price_desc"):
        products = search_by_price(
            query=query,
            kategori=intent.kategori,
            max_harga=intent.max_harga,
            ascending=(intent.sort == "price_asc"),
            top_k=SEARCH_LIMIT,
        )
    elif not has_keywords(query):
        # "ada produk apa aja" / "lihat semua" -> browse semua (terfilter)
        products = list_all(
            kategori=intent.kategori,
            max_harga=intent.max_harga,
            limit=SEARCH_LIMIT,
        )
    else:
        products = search_ranked(
            query=query,
            kategori=intent.kategori,
            max_harga=intent.max_harga,
            limit=SEARCH_LIMIT,
        )
    if not products:
        return [_NOT_FOUND]

    # simpan SELURUH hasil utk paginasi 'lanjut', lalu tampilkan halaman pertama
    set_search(user, [p["id"] for p in products])
    batch, remaining = next_search_page(user, PAGE_SIZE)
    return _render_page(_lead_in(intent, text), batch, remaining, user)


def _do_more(user: str) -> list[str]:
    batch, remaining = next_search_page(user, PAGE_SIZE)
    if not batch:
        return [
            "Itu semua produk yang aku punya buat pencarian tadi 🙏 "
            "Mau cari yang lain?"
        ]
    return _render_page(_pick(_LEAD_MORE, str(remaining)), batch, remaining, user)


def _do_resend(user: str) -> list[str]:
    """'kirim ulang katalog' -> tampilkan lagi hasil pencarian dari halaman 1."""
    if not reset_search(user):
        return [
            "Belum ada katalog yang bisa dikirim ulang 🙏 "
            "Cari dulu ya, misal *cari gamis*."
        ]
    batch, remaining = next_search_page(user, PAGE_SIZE)
    if not batch:
        return ["Belum ada katalog 🙏 Cari dulu ya, misal *cari gamis*."]
    return _render_page("Ini lagi katalognya 👇", batch, remaining, user)


def _ref_list_from_quoted(quoted: str) -> list[str]:
    """Kalau user me-reply pesan berisi produk, ambil daftar produk dari LINK di
    pesan itu (urut sesuai aslinya) supaya 'no 3' / nama tetap merujuk ke situ."""
    if not quoted:
        return []
    links = re.findall(r"https?://\S+", quoted)
    if not links:
        return []
    by_link = {p.get("link", ""): p["id"] for p in all_products() if p.get("link")}
    ids: list[str] = []
    for raw in links:
        link = raw.rstrip(").,>\"'")
        pid = by_link.get(link)
        if pid and pid not in ids:
            ids.append(pid)
    return ids


def _bare_number_refs(text: str) -> list[str]:
    """Deteksi pesan yang isinya cuma nomor (mis '1,2,5', 'no 1 dan 3')."""
    t = re.sub(
        r"\b(simpan|masukin|masukkan|no|nomor|mau|ambil|beli|dan|sama|juga|"
        r"yang|ya|dong|aja|tolong)\b",
        " ",
        text.lower(),
    ).strip()
    if t and re.fullmatch(r"[\s,\.\-0-9]+", t) and re.search(r"\d", t):
        return re.findall(r"\d+", t)
    return []


def _variant_lines(p: dict) -> list[str]:
    """Baris ukuran & warna dari data varian produk (kosong kalau tak ada)."""
    v = p.get("variants") or {}
    out = []
    if v.get("sizes"):
        out.append("  Ukuran: " + ", ".join(v["sizes"]))
    if v.get("colors"):
        out.append("  Warna: " + ", ".join(v["colors"]))
    return out


def _do_product_detail(intent: Intent, user: str) -> str:
    """Jawab pertanyaan detail (ukuran/warna/stok) dari data varian produk."""
    # tentukan produk target: rujukan eksplisit dulu, lalu daftar terakhir
    products = []
    if intent.product_ref:
        pid = _resolve_product_id(user, intent.product_ref)
        if pid:
            p = get_product(pid)
            if p:
                products = [p]
    if not products:
        products = [p for p in (get_product(i) for i in get_last_list(user)) if p][:3]

    if not products:
        return (
            "Mau tanya detail produk yang mana ya? 🙂\n"
            "Cari dulu yuk, misal ketik *cari kaos*."
        )

    lines = ["Ini detail varian yang tersedia 👕", ""]
    for p in products:
        lines.append(f"*{p['nama']}* — Rp{p['harga']:,}")
        vlines = _variant_lines(p)
        if vlines:
            lines += vlines
        else:
            lines.append("  Detail lengkap ada di: " + p["link"])
        lines.append("")
    lines.append("Ketik *simpan no <x>* untuk masukkan keranjang, atau *bayar*. 🛍️")
    return "\n".join(lines).strip()


# ---------- keranjang ----------

def _resolve_product_id(user: str, ref: Optional[str]) -> Optional[str]:
    """Ubah product_ref (nomor / nama) jadi product_id."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if ref.isdigit():
        idx = int(ref) - 1
        last = get_last_list(user)
        if 0 <= idx < len(last):
            return last[idx]
        return None
    matches = find_by_name(ref)
    return matches[0]["id"] if matches else None


_ALL_REFS = {"semua", "semuanya", "semua nya", "all", "semua dong", "semua ny"}


def _do_add(intent: Intent, user: str):
    ref = (intent.product_ref or "").strip().lower()

    # "beli semua / ambil semua" -> masukin SEMUA produk yang lagi ditampilkan
    if ref in _ALL_REFS:
        ids = get_last_list(user)
        products = [p for p in (get_product(i) for i in ids) if p]
        if not products:
            return (
                "Mau ambil yang mana ya? 😅 Cari dulu yuk, "
                "misal *cari gamis*, baru bisa ambil semua."
            )
        for p in products:
            add_to_cart(user, p["id"])
        return [f"Sip, {len(products)} produk aku masukin keranjang! 🛍️", _do_view(user)]

    # multi-pilih: "1,2,5" / "1 2 5" -> masukin beberapa sekaligus
    nums = re.findall(r"\d+", ref)
    if len(nums) >= 2:
        last = get_last_list(user)
        added = []
        for n in nums:
            i = int(n) - 1
            if 0 <= i < len(last) and last[i] not in added:
                add_to_cart(user, last[i])
                added.append(last[i])
        if not added:
            return (
                "Hmm, nomornya nggak ada di daftar 😅 "
                "Coba lihat daftarnya lagi ya (*kirim ulang katalog*)."
            )
        return [f"Sip, {len(added)} produk masuk keranjang! 🛍️", _do_view(user)]

    pid = _resolve_product_id(user, intent.product_ref)
    if not pid:
        return (
            "Eh, aku belum tau yang mana yang mau disimpan 😅\n"
            "Cari dulu yuk (*cari gamis harga 50rb*), terus ketik *simpan no 1*."
        )
    add_to_cart(user, pid)
    p = get_product(pid)
    return (
        f"✅ *{p['nama']}* (Rp{p['harga']:,}) masuk keranjang.\n"
        "Ketik *keranjang saya* untuk lihat, atau *bayar* untuk checkout. 🛒"
    )


def _format_cart(items: list[dict]) -> str:
    lines = ["🛒 *Keranjang kamu:*", ""]
    total = 0
    for i, it in enumerate(items, 1):
        p = it["product"]
        sub = p["harga"] * it["qty"]
        total += sub
        lines.append(f"{i}. {p['nama']} x{it['qty']} = Rp{sub:,}")
    lines += ["", f"*Total: Rp{total:,}*", ""]
    lines.append("Ketik *bayar* untuk checkout, atau *hapus no <x>* untuk hapus item.")
    return "\n".join(lines)


def _do_view(user: str) -> str:
    items = get_cart(user)
    if not items:
        return (
            "Keranjang kamu masih kosong 🛒\n"
            "Ketik *cari <produk>* untuk mulai belanja."
        )
    # samakan nomor di tampilan keranjang dengan rujukan 'hapus no <x>'
    set_last_list(user, [it["product"]["id"] for it in items])
    return _format_cart(items)


def _do_remove(intent: Intent, user: str) -> str:
    ref = (intent.product_ref or "").strip().lower()
    if not ref or ref in {"semua", "semuanya", "all", "kosong", "kosongkan"}:
        clear_cart(user)
        return "Keranjang dikosongkan 🗑️"

    pid = _resolve_product_id(user, intent.product_ref)
    if not pid:
        return "Hmm, itu nggak ketemu di keranjang 😅 Coba ketik *keranjang saya* dulu ya."
    p = get_product(pid)
    remove_from_cart(user, pid)
    return f"🗑️ *{p['nama']}* dihapus dari keranjang.\nKetik *keranjang saya* untuk cek."


def _do_checkout(user: str) -> str:
    items = get_cart(user)
    if not items:
        return (
            "Keranjangnya masih kosong nih 🛒 Yuk cari dulu, simpan, baru *bayar* ya."
        )
    total = sum(it["product"]["harga"] * it["qty"] for it in items)
    order_id = f"INV-{int(time.time())}"
    lines = [f"🧾 *Order {order_id}*", ""]
    for it in items:
        p = it["product"]
        lines.append(f"• {p['nama']} x{it['qty']} = Rp{p['harga'] * it['qty']:,}")
    lines += [
        "",
        f"*Total bayar: Rp{total:,}*",
        "",
        "💳 *Pembayaran (dummy):*",
        f"Transfer ke BCA 1234567890 a/n {STORE_NAME}",
        f"Sertakan kode *{order_id}* di berita transfer.",
        "",
        "Setelah transfer, kirim bukti ke chat ini ya. Terima kasih! 🙏",
    ]
    clear_cart(user)  # order dibuat, keranjang dikosongkan (dummy)
    return "\n".join(lines)


# ---------- guard ----------

def _truncate(text: str) -> str:
    """Batasi panjang balasan supaya rapi di WhatsApp."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[: MAX_OUTPUT_CHARS - 1].rstrip() + "…"


def _pick(options: list[str], seed: str) -> str:
    """Pilih satu variasi secara deterministik dari teks (biar nggak monoton)."""
    return options[sum(map(ord, seed)) % len(options)]


_GREETINGS = [
    f"Halo! 👋 Selamat datang di *{STORE_NAME}*. Lagi cari pakaian apa nih?",
    f"Hai! 😊 Aku admin *{STORE_NAME}*, siap bantu cariin baju buat kamu.",
    f"Halo, makasih udah mampir ke *{STORE_NAME}*! 🛍️ Mau cari apa hari ini?",
]

_OUT_OF_SCOPE = [
    "Hmm, kalau soal itu aku kurang bisa bantu ya 😅",
    "Waduh, itu di luar yang bisa aku jawab nih 🙏",
    "Maaf ya, untuk yang itu aku belum bisa bantu 😅",
]

_THANKS = [
    "Sama-sama! 🙏 Kalau butuh apa-apa lagi, bilang aja ya. 😊",
    "Dengan senang hati! 😊 Mau lihat-lihat yang lain juga boleh.",
    "Siap, sama-sama! 🙏 Semoga belanjanya menyenangkan di TokoEko. 🛍️",
]


def _greet(text: str) -> str:
    return _pick(_GREETINGS, text) + "\n\nContoh: ketik *cari gamis harga 50rb* ya. 🙂"


def _out_of_scope(text: str) -> str:
    return (
        _pick(_OUT_OF_SCOPE, text)
        + f" Soalnya di sini aku khusus bantu belanja pakaian di *{STORE_NAME}* aja.\n\n"
        "Tapi kalau kamu lagi nyari baju, langsung aja — misal *cari gamis harga 50rb*. 🛍️"
    )


# ---------- balasan obrolan (LLM, biar manusiawi) ----------

_CONVERSE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            f"Kamu admin {STORE_NAME} (toko pakaian), ngobrol di WhatsApp kayak "
            "temen akrab. Bahasa Indonesia santai/gaul tapi sopan.\n"
            "ATURAN GAYA (penting):\n"
            "- SANGAT singkat: maksimal 1 kalimat pendek + 1 ajakan pendek.\n"
            "- Santai & luwes. Pakai 'aku/kamu', kata kayak 'nih', 'ya', 'dong', "
            "'cariin', 'sip'. Boleh 1 emoji.\n"
            "- HINDARI bahasa kaku/formal/bertele-tele dan kalimat panjang.\n"
            "- JANGAN mengarang promo/diskon/harga/stok/produk yang nggak ada.\n"
            "- Kalau user mau pindah ke toko/marketplace lain (Shopee, Tokopedia, "
            "dll) atau mau pergi: JANGAN dukung pindah/'silakan'. Tahan dengan "
            "playful & percaya diri, tawarin coba cari di sini dulu. Contoh: 'Yah "
            "jangan ke sebelah dong 😆 di TokoEko juga lengkap kok, mau cari apa?'\n"
            "Contoh BENAR (tiru gaya ini):\n"
            "  • 'Haha santai 😄 Lagi cari apa nih? Aku bantuin cariin ya.'\n"
            "  • 'Sipp 🙌 mau lihat yang lain juga?'\n"
            "  • 'Sama-sama! 🙏 Ada lagi yang dicari?'\n"
            "Contoh SALAH (jangan begini, kaku/panjang):\n"
            "  • 'Apa yang kamu cari? Mau aku bantu cari produk yang kamu inginkan?'\n"
            "  • 'Haha, jangan pusing, aku ada di sini! Apa yang bisa saya bantu?'\n"
            "{mode}",
        ),
        MessagesPlaceholder("history"),
        ("human", "{text}"),
    ]
)

_SOCIAL = (
    "Konteks: user lagi menyapa / berterima kasih / kasih reaksi santai "
    "(mis. 'mantap', 'ok', 'keren'). Tanggapi hangat & nyambung sama obrolan."
)
_DECLINE = (
    "Konteks: user nanya/minta hal DI LUAR belanja pakaian di toko ini. Tolak "
    "dengan ramah & santai, JANGAN jawab pertanyaannya, arahkan balik ke belanja."
)


def _converse(text: str, history: list[dict], mode: str, social: bool) -> str:
    try:
        return _invoke_llm(
            lambda llm: _CONVERSE_PROMPT | llm,
            {"text": text, "history": _to_messages(history or []), "mode": mode},
        ).content
    except Exception as exc:  # kalau LLM gagal, jatuh ke template
        print(f"[converse] fallback template: {exc}")
        return _greet(text) if social else _out_of_scope(text)


_GIBBERISH = [
    "Hehe, kayaknya kepencet ya? 😄 Mau cari apa nih?",
    "Wkwk pesannya ngasal 😅 Lagi nyari produk apa?",
    "Hmm itu apa ya? 😄 Ketik aja mau cari apa, aku bantuin.",
]

_INAPPROPRIATE = [
    "Eh eh, jaga omongan ya 🙏 Di sini cuma buat belanja di TokoEko kok 😊",
    "Hush, jangan gitu dong 😅 Yuk fokus cari produk aja, mau cari apa?",
    "Waduh, kata-katanya 🙏 Di sini khusus belanja ya. Lagi nyari apa nih?",
]

# kata kasar/jorok yang jelas — dicek deterministik (jalan walau LLM mati).
# Sengaja yang nggak ambigu; kasus halus (SARA/seksual) diserahkan ke LLM.
_BADWORDS = {
    "anjing", "anjeng", "anjg", "asu", "bangsat", "bajingan", "bangke",
    "kontol", "kontl", "memek", "pepek", "ngentot", "entot", "ngewe", "coli",
    "jancok", "jancuk", "pukimak", "puki", "lonte", "perek", "sundal",
    "pelacur", "kampang", "goblok", "tolol", "bego", "ngehe",
}


def _norm_word(tok: str) -> str:
    """Hilangkan huruf berulang biar 'anjenggg'/'asuu' tetap kena."""
    return re.sub(r"(.)\1+", r"\1", tok)


def _is_inappropriate(text: str) -> bool:
    for tok in re.findall(r"[a-z]+", text.lower()):
        if tok in _BADWORDS or _norm_word(tok) in _BADWORDS:
            return True
    return False


# perintah pendek yang sangat umum: tangani deterministik (tanpa LLM) supaya
# tidak salah klasifikasi & lebih cepat/hemat.
_QUICK = {
    "more": {
        "lanjut", "lanjutkan", "next", "lagi", "ada lagi", "lihat lagi",
        "yang lain", "lainnya", "selanjutnya", "more",
    },
    "view_cart": {"keranjang", "keranjang saya", "cek keranjang", "lihat keranjang"},
    "checkout": {"bayar", "checkout", "pesan sekarang", "pesan"},
    "resend": {
        "kirim ulang", "kirim ulang katalog", "kirim ulang katalognya", "katalog",
        "katalognya", "ulangi", "ulang", "tampilkan lagi", "kirim lagi",
    },
    "help": {"help", "bantuan", "cara order", "menu"},
}


def _quick_action(text: str) -> Optional[str]:
    t = text.strip().lower().strip("?.!,")
    for action, phrases in _QUICK.items():
        if t in phrases:
            return action
    return None


def _route(text: str, user: str, history: list[dict]):
    """Return str (1 bubble) atau list[str] (multi-bubble)."""
    # fast-path perintah umum (hindari variansi klasifikasi LLM)
    quick = _quick_action(text)
    if quick == "more":
        return _do_more(user)
    if quick == "view_cart":
        return _do_view(user)
    if quick == "checkout":
        return _do_checkout(user)
    if quick == "resend":
        return _do_resend(user)
    if quick == "help":
        return HELP_TEXT

    # fast-path multi-pilih nomor ("1,2,5") -> masukin keranjang
    nums = _bare_number_refs(text)
    if nums:
        return _do_add(Intent(action="add_to_cart", product_ref=",".join(nums)), user)

    intent = extract_intent(text, history)

    if intent.action == "search":
        return _do_search(text, intent, user)
    if intent.action == "add_to_cart":
        return _do_add(intent, user)
    if intent.action == "view_cart":
        return _do_view(user)
    if intent.action == "remove_from_cart":
        return _do_remove(intent, user)
    if intent.action == "checkout":
        return _do_checkout(user)
    if intent.action == "more":
        return _do_more(user)
    if intent.action == "product_detail":
        return _do_product_detail(intent, user)
    if intent.action == "help":
        return HELP_TEXT
    if intent.action == "gibberish":
        return _pick(_GIBBERISH, text)
    if intent.action == "inappropriate":
        return _pick(_INAPPROPRIATE, text)
    if intent.action == "out_of_scope":
        return _converse(text, history, _DECLINE, social=False)
    if intent.action in ("greeting", "thanks", "chitchat"):
        return _converse(text, history, _SOCIAL, social=True)

    # fallback
    return _converse(text, history, _SOCIAL, social=True)


# ---------- entry point ----------

def handle_message(text: str, user: str = "anon", quoted: str = "") -> list[str]:
    """Return daftar bubble balasan (bisa >1 pesan untuk dikirim terpisah).

    quoted = teks pesan yang di-reply user (kalau ada). Kalau pesan itu berisi
    produk, jadikan acuan supaya 'no 3'/nama merujuk ke pesan tsb.
    """
    text = (text or "").strip()

    # konteks reply: produk di pesan yang dibalas jadi acuan terbaru
    try:
        qids = _ref_list_from_quoted(quoted)
        if qids:
            set_last_list(user, qids)
    except Exception as exc:
        print(f"[handle_message] gagal baca quoted: {exc}")

    # guard: pesan kosong
    if not text:
        return [_greet("")]

    # guard: input kepanjangan (anti-spam / abuse)
    if len(text) > MAX_INPUT_CHARS:
        return [
            "Waduh, pesannya panjang banget 😅 boleh diringkas dikit? "
            "Misal: *cari gamis harga 50rb*."
        ]

    # guard: kata kasar/jorok (deterministik, sebelum LLM)
    if _is_inappropriate(text):
        return [_pick(_INAPPROPRIATE, text)]

    # ambil riwayat (best-effort; kalau gagal, jalan tanpa memory)
    try:
        history = memory.get_history(user)
    except Exception as exc:
        print(f"[handle_message] gagal baca history: {exc}")
        history = []

    try:
        result = _route(text, user, history)
    except Exception as exc:  # jaga-jaga LLM/Qdrant/Redis error
        print(f"[handle_message] error: {exc}")
        result = "Aduh, maaf ya lagi ada kendala sebentar 🙏 boleh coba ulangi lagi?"

    messages = result if isinstance(result, list) else [result]
    messages = [_truncate(m) for m in messages if m and m.strip()]
    if not messages:
        messages = ["Maaf, ada yang salah 🙏 coba lagi ya."]

    # simpan percakapan (best-effort, tidak boleh ganggu balasan)
    try:
        memory.append(user, "user", text)
        memory.append(user, "bot", " ".join(messages))
    except Exception as exc:
        print(f"[handle_message] gagal simpan history: {exc}")

    return messages
