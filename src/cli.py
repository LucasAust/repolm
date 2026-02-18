"""
RepoLM — CLI Entry Point
Usage:
    python cli.py <github_url> [--format overview,podcast,slides] [--model gemini-2.5-pro] [--audio]
"""

import argparse
import os
import sys

from ingest import ingest_repo, repo_to_text
from summarize import run_pipeline
from podcast_audio import generate_audio


def main():
    parser = argparse.ArgumentParser(
        description="RepoLM — Turn any GitHub repo into learning content",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py https://github.com/expressjs/express
  python cli.py https://github.com/redis/redis --format overview,podcast --audio
  python cli.py https://github.com/facebook/react --model gemini-2.5-pro-mini
        """
    )
    parser.add_argument("url", help="GitHub repository URL")
    parser.add_argument("--format", default="overview,podcast,slides",
                        help="Output formats (comma-separated): overview, podcast, slides")
    parser.add_argument("--model", default="gemini-2.5-pro",
                        help="LLM model to use (default: gemini-2.5-pro)")
    parser.add_argument("--audio", action="store_true",
                        help="Generate podcast audio (requires edge-tts or elevenlabs)")
    parser.add_argument("--output", default="output",
                        help="Output directory (default: output)")

    args = parser.parse_args()
    formats = [f.strip() for f in args.format.split(",")]

    print(f"🦞 RepoLM — Ingesting {args.url}\n")

    # Step 1: Ingest
    data = ingest_repo(args.url)
    repo_text = repo_to_text(data)

    raw_path = os.path.join(args.output, f"{data.name}_raw.txt")
    os.makedirs(args.output, exist_ok=True)
    with open(raw_path, "w") as f:
        f.write(repo_text)

    print(f"\nIngested: {len(data.files)} files, {data.total_chars:,} chars")
    print(f"Languages: {', '.join(sorted(data.language_stats.keys()))}")

    # Step 2: Summarize
    print(f"\nGenerating: {', '.join(formats)}...")
    results = run_pipeline(raw_path, output_dir=args.output, model=args.model, formats=formats)

    # Step 3: Audio (optional)
    if args.audio and "podcast" in results:
        print("\nGenerating podcast audio...")
        generate_audio(results["podcast"], output_dir=args.output)

    print("\n✅ Done! Check the output/ directory.")


if __name__ == "__main__":
    main()
