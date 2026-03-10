# swe_agent/__main__.py
"""
Entry point for running swe_agent as a module.
Usage:
  python -m swe_agent.runner --project Lang --bug 1 --baseline agentless
  python -m swe_agent.eval --bugs benchmarks/defects4j_small.txt --baseline agentless
"""
import sys
import argparse


def main():
    parser = argparse.ArgumentParser(
        description="SWE-Agent: Multi-Baseline APR for Defects4J",
        prog="swe_agent"
    )
    parser.add_argument(
        "command",
        choices=["runner", "eval"],
        help="Command to run: 'runner' for single bug, 'eval' for batch evaluation"
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments to pass to the command"
    )

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
