"""Terminal chat for testing the agent. Run from the backend folder:
    python3 chat_cli.py              (Gemini + tools)
    python3 chat_cli.py --scripted   (no AI, scripted fallback)
Type 'quit' to exit."""
import sys

from agent.agent import BookingAgent
from agent.llm import GeminiProvider


def main() -> None:
    scripted = "--scripted" in sys.argv
    if not scripted and not GeminiProvider.has_key():
        print("(No GEMINI_API_KEY found in .env, using scripted mode)")
        scripted = True
    agent = BookingAgent(force_scripted=scripted)
    print("Smile Dental Clinic booking assistant. Type 'quit' to exit.\n")
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"quit", "exit"}:
            break
        if not text:
            continue
        answer = agent.reply(text)
        for event in agent.events:
            print(f"  [debug] {event}")
        print(f"Bot: {answer}\n")


if __name__ == "__main__":
    main()