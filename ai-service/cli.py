"""Tes bot lewat terminal tanpa perlu WhatsApp.

    python cli.py
    > cari gamis harga 50rb
"""
from agent import handle_message


def main() -> None:
    print("Ketik pesan (Ctrl+C untuk keluar)\n")
    try:
        while True:
            text = input("kamu > ").strip()
            if not text:
                continue
            for msg in handle_message(text, user="cli-user"):
                print("bot >", msg)
            print()
    except (KeyboardInterrupt, EOFError):
        print("\nDadah! 👋")


if __name__ == "__main__":
    main()
