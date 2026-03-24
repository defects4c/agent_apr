# single_shot_thought/__main__.py
"""
Entry point:
  python -m single_shot_thought runner --project Lang --bug 1 --baseline cot
  python -m single_shot_thought eval --bugs bugs.txt --baseline cot react
"""
import sys
import argparse


def main():
    parser = argparse.ArgumentParser(prog="single_shot_thought")
    parser.add_argument("command", choices=["runner", "eval"])
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.command == "runner":
        from . import runner
        sys.argv = ["runner"] + args.args
        runner.main()
    elif args.command == "eval":
        from . import eval as eval_module
        sys.argv = ["eval"] + args.args
        eval_module.main()


if __name__ == "__main__":
    main()
