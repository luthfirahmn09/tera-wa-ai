// WhatsApp gateway (Baileys): dengar pesan masuk -> kirim ke AI service -> balas.
// Tanpa browser/Chromium. QR untuk login diexpose di http://localhost:3000/qr
require("dotenv").config();
const fs = require("fs");
const http = require("http");
const QRCode = require("qrcode");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  downloadMediaMessage,
  DisconnectReason,
} = require("@whiskeysockets/baileys");
const axios = require("axios");

const AI_SERVICE_URL = process.env.AI_SERVICE_URL || "http://localhost:6001";
const SESSION_PATH = process.env.SESSION_PATH || "/app/session";
const AUTH_DIR = `${SESSION_PATH}/baileys_auth`;
const QR_PORT = parseInt(process.env.QR_PORT || "6002", 10);

let latestQR = null; // data URL QR terbaru
let qrId = 0; // naik tiap QR berganti (buat cache-bust gambar tanpa flicker)
let connected = false;

// logger diam supaya log tidak banjir (Baileys butuh objek logger pino-like)
const silentLogger = {
  level: "silent",
  trace() {},
  debug() {},
  info() {},
  warn() {},
  error() {},
  fatal() {},
  child() {
    return silentLogger;
  },
};

// ---------- halaman QR ----------

function qrPage() {
  return `<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TokoEko — Scan WhatsApp</title>
<style>
  body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#ECE5DD;
       margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{background:#fff;border-radius:14px;box-shadow:0 6px 24px rgba(0,0,0,.15);
        padding:28px 32px;text-align:center;max-width:360px}
  h1{color:#075E54;font-size:20px;margin:0 0 4px}
  p{color:#555;font-size:14px;margin:6px 0}
  img{width:280px;height:280px;object-fit:contain;margin:12px 0}
  .ok{color:#128C7E;font-size:48px;margin:10px 0}
  .muted{font-size:12px;color:#999}
</style></head><body>
<div class="card" id="card"><h1>TokoEko WhatsApp</h1><p>memuat…</p></div>
<script>
// render ulang HANYA kalau state berubah -> gambar QR tidak kedip tiap polling
let last = null;
async function tick(){
  try{
    const s = await (await fetch('/status',{cache:'no-store'})).json();
    const key = s.connected ? 'on' : (s.hasQR ? 'qr'+s.qrId : 'wait');
    if(key === last) return;          // tidak ada perubahan -> jangan sentuh DOM
    last = key;
    const card = document.getElementById('card');
    if(s.connected){
      card.innerHTML = '<h1>TokoEko WhatsApp</h1><div class="ok">✅</div>'+
        '<p><b>Tersambung!</b> Bot siap menerima pesan.</p>'+
        '<p class="muted">Halaman ini boleh ditutup.</p>';
    } else if(s.hasQR){
      card.innerHTML = '<h1>Scan untuk login</h1>'+
        '<p>Buka WhatsApp → Perangkat tertaut → Tautkan perangkat</p>'+
        '<img src="/qr.png?v='+s.qrId+'" alt="QR">'+
        '<p class="muted">QR berganti otomatis kalau kedaluwarsa.</p>';
    } else {
      card.innerHTML = '<h1>TokoEko WhatsApp</h1><p>menyiapkan QR…</p>'+
        '<p class="muted">tunggu sebentar lalu halaman akan update.</p>';
    }
  }catch(e){ /* abaikan, coba lagi */ }
}
tick(); setInterval(tick, 3000);
</script></body></html>`;
}

function startQrServer() {
  http
    .createServer((req, res) => {
      const url = (req.url || "/").split("?")[0];
      if (url === "/" || url === "/qr") {
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        res.end(qrPage());
      } else if (url === "/qr.png") {
        if (!latestQR) {
          res.writeHead(404);
          res.end();
          return;
        }
        const b64 = latestQR.split(",")[1] || "";
        res.writeHead(200, { "Content-Type": "image/png", "Cache-Control": "no-store" });
        res.end(Buffer.from(b64, "base64"));
      } else if (url === "/status") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ connected, hasQR: !!latestQR, qrId }));
      } else {
        res.writeHead(404);
        res.end();
      }
    })
    .listen(QR_PORT, () =>
      console.log(`🔗 Halaman QR: http://localhost:${QR_PORT}/qr`)
    );
}

// ---------- AI ----------

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function askAI(text, from, quoted) {
  const { data } = await axios.post(
    `${AI_SERVICE_URL}/chat`,
    { text, from_: from, quoted: quoted || "" },
    { timeout: 30000 }
  );
  // dukung multi-bubble; fallback ke reply tunggal
  return data.messages && data.messages.length ? data.messages : [data.reply];
}

function toMessages(data) {
  return data.messages && data.messages.length ? data.messages : [data.reply];
}

// Voice note: kirim audio (base64 OGG) ke ai-service buat ditranskrip + dijawab.
async function handleVoice(sock, from, msg) {
  console.log(`[masuk-voice] ${from}`);
  try {
    await sock.sendPresenceUpdate("composing", from).catch(() => {});
    const buffer = await downloadMediaMessage(
      msg,
      "buffer",
      {},
      { logger: silentLogger, reuploadRequest: sock.updateMediaMessage }
    );
    const { data } = await axios.post(
      `${AI_SERVICE_URL}/chat`,
      { from_: from, audio_base64: buffer.toString("base64") },
      { timeout: 60000 }
    );
    // tampilkan hasil transkrip dulu (biar user tau yg kebaca), lalu balasan
    if (data.transcript) {
      await sock.sendMessage(from, { text: `🎙️ _"${data.transcript}"_` });
      await sleep(500);
    }
    const msgs = toMessages(data);
    for (let i = 0; i < msgs.length; i++) {
      await sock.sendMessage(from, { text: msgs[i] });
      if (i < msgs.length - 1) await sleep(BUBBLE_GAP_MS);
    }
  } catch (err) {
    console.error("Gagal proses voice:", err.message);
    await sock.sendMessage(from, {
      text: "Maaf, suaranya nggak kebaca 🙏 coba ketik aja ya.",
    });
  }
}

// Debounce pesan beruntun: kalau user kirim cepat 2-3x, gabung jadi satu
// sebelum diproses (lebih natural & hemat panggilan AI).
const DEBOUNCE_MS = 1200;
const BUBBLE_GAP_MS = 800;
const buffers = new Map(); // jid -> { texts:[], timer }

function enqueue(sock, from, text, quoted) {
  let b = buffers.get(from);
  if (!b) { b = { texts: [], timer: null, quoted: "" }; buffers.set(from, b); }
  b.texts.push(text);
  if (quoted) b.quoted = quoted; // pesan yang di-reply (yang terakhir menang)
  if (b.timer) clearTimeout(b.timer);
  b.timer = setTimeout(() => {
    buffers.delete(from);
    handleCombined(sock, from, b.texts.join("\n"), b.quoted).catch((e) =>
      console.error("handleCombined:", e.message)
    );
  }, DEBOUNCE_MS);
}

async function handleCombined(sock, from, text, quoted) {
  console.log(`[proses] ${from}: ${text.replace(/\n/g, " | ")}`);
  try {
    await sock.sendPresenceUpdate("composing", from).catch(() => {});
    const msgs = await askAI(text, from, quoted);
    for (let i = 0; i < msgs.length; i++) {
      await sock.sendMessage(from, { text: msgs[i] });
      if (i < msgs.length - 1) {
        await sleep(BUBBLE_GAP_MS);
        await sock.sendPresenceUpdate("composing", from).catch(() => {});
      }
    }
  } catch (err) {
    console.error("Gagal proses pesan:", err.message);
    await sock.sendMessage(from, {
      text: "Maaf, lagi ada gangguan 🙏 coba lagi sebentar ya.",
    });
  }
}

function extractText(msg) {
  const m = msg.message || {};
  return (
    m.conversation ||
    m.extendedTextMessage?.text ||
    m.imageMessage?.caption ||
    m.videoMessage?.caption ||
    ""
  );
}

// teks pesan yang di-reply user (kalau ada quote)
function extractQuoted(msg) {
  const q = msg.message?.extendedTextMessage?.contextInfo?.quotedMessage;
  if (!q) return "";
  return (
    q.conversation ||
    q.extendedTextMessage?.text ||
    q.imageMessage?.caption ||
    q.videoMessage?.caption ||
    ""
  );
}

// ---------- WhatsApp (Baileys) ----------

async function startSock() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  let version;
  try {
    ({ version } = await fetchLatestBaileysVersion());
  } catch (_) {
    version = undefined; // pakai versi bawaan Baileys kalau gagal fetch
  }

  const sock = makeWASocket({
    version,
    auth: state,
    logger: silentLogger,
    printQRInTerminal: false,
    browser: ["TokoEko", "Chrome", "1.0.0"],
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      latestQR = await QRCode.toDataURL(qr);
      qrId++;
      connected = false;
      console.log(`🔗 QR baru — scan di http://localhost:${QR_PORT}/qr`);
    }

    if (connection === "open") {
      connected = true;
      latestQR = null;
      console.log("✅ WA tersambung. Bot siap menerima pesan.");
    }

    if (connection === "close") {
      connected = false;
      const code = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = code === DisconnectReason.loggedOut;
      console.log(
        `Koneksi putus (code ${code}). ${loggedOut ? "Logged out — minta QR baru." : "Mencoba reconnect…"}`
      );
      if (loggedOut) {
        // sesi tidak valid lagi: hapus auth supaya muncul QR baru
        try {
          fs.rmSync(AUTH_DIR, { recursive: true, force: true });
        } catch (_) {}
      }
      startSock();
    }
  });

  sock.ev.on("messages.upsert", ({ messages, type }) => {
    if (type !== "notify") return;
    for (const msg of messages) {
      if (!msg.message || msg.key.fromMe) continue;
      const from = msg.key.remoteJid;
      // abaikan grup, status, dan broadcast
      if (!from || from.endsWith("@g.us") || from === "status@broadcast") continue;

      // voice note -> jalur transkrip (di luar debounce teks)
      if (msg.message.audioMessage) {
        handleVoice(sock, from, msg).catch((e) =>
          console.error("handleVoice:", e.message)
        );
        continue;
      }

      const text = extractText(msg).trim();
      if (!text) continue;

      const quoted = extractQuoted(msg);
      console.log(`[masuk] ${from}: ${text}${quoted ? " (reply)" : ""}`);
      enqueue(sock, from, text, quoted); // debounce + proses gabungan
    }
  });
}

startQrServer();
startSock().catch((e) => {
  console.error("Gagal start Baileys:", e);
  process.exit(1);
});
