import asyncio
import sys
from app.evals.runner import runner


async def main():
    version = sys.argv[1] if len(sys.argv) > 1 else "v2"
    summary = await runner.run_evaluation(prompt_version=version)
    if summary["mean_faithfulness"] < 0.7:
        print("Warning: Faithfulness below target threshold (0.7)")
        sys.exit(1)
    print("Evaluation benchmark passed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
