"""Dispatch draft and gameplay; a failed turn is logged and still yields."""
import cambc
import draft
from bot import Bot


# The S2.3 utility bot explicitly opts into the extended draft-memory
# format.  The frozen baseline bot has no S23_ENABLED marker, so this
# remains a no-op for that path and preserves its legacy payload.
if getattr(Bot, "S23_ENABLED", False):
    draft.enable_s23()


def main():
    bot = Bot()
    while True:
        try:
            if cambc.is_draft():
                draft.play()
            else:
                bot.play_turn()
        except Exception as error:
            print(f"bot error: {error}", flush=True)

        cambc.end_turn()


if __name__ == "__main__":
    main()
