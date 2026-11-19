#!/usr/bin/env python3
"""Terminal chat with AirGuide: python cli.py"""
from airguide.assistant import respond
from airguide.context import UserContext


def main() -> None:
    print("AirGuide - Urban Air Quality Assistant (type 'quit' to exit)")
    print("Tell me your city or today's AQI to begin.")
    state = UserContext()
    while True:
        try:
            message = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if message.lower() in {"quit", "exit"}:
            break
        if not message:
            continue
        reply = respond(message, state)
        state = reply.state
        print(reply.text, end="\n\n")


if __name__ == "__main__":
    main()
