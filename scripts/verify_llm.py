#!/usr/bin/env python3
"""Verification script for LLM integration.

This script verifies that the DeepSeek API integration is working by:
1. Loading API key from environment
2. Sending a simple prompt to the API
3. Receiving and displaying the response

Usage:
    python scripts/verify_llm.py

Requirements:
    - Set DEEPSEEK_API_KEY in .env file or environment variable

Note:
    DeepSeek API is compatible with OpenAI API format.
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def print_header(title: str) -> None:
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


async def verify_llm() -> bool:
    """Verify LLM integration.

    Returns:
        True if all verifications pass, False otherwise.
    """
    print_header("LLM Verification Script")

    # Step 1: Load configuration
    print("\n[1/4] Loading configuration...")
    try:
        from flightscanner.utils.config import settings

        if not settings.deepseek_api_key or settings.deepseek_api_key == "sk-your-deepseek-api-key-here":
            print("✗ DeepSeek API key not configured!")
            print("  Please set DEEPSEEK_API_KEY in .env file or environment variable")
            return False

        print(f"✓ Configuration loaded")
        print(f"  Model: {settings.deepseek_model}")
        print(f"  Base URL: {settings.deepseek_base_url}")
        print(f"  API Key: {settings.deepseek_api_key[:10]}...")

    except Exception as e:
        print(f"✗ Failed to load configuration: {e}")
        return False

    # Step 2: Initialize DeepSeek client (OpenAI-compatible)
    print("\n[2/4] Initializing DeepSeek client...")
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        print("✓ DeepSeek client initialized (OpenAI-compatible)")

    except ImportError:
        print("✗ OpenAI package not installed!")
        print("  Please install: pip install openai")
        return False
    except Exception as e:
        print(f"✗ Failed to initialize DeepSeek client: {e}")
        return False

    # Step 3: Send test prompt
    print("\n[3/4] Sending test prompt to DeepSeek API...")
    try:
        test_prompt = """
你是一位机票价格分析专家。请分析以下航班信息并给出简短建议:

航班: CA1234 北京 -> 上海
日期: 2024-02-15
当前价格: 680元 (经济舱)
历史平均价格: 850元

请用1-2句话说明这个价格是否值得购买。
"""

        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {
                    "role": "system",
                    "content": "你是一位专业的机票价格分析师，擅长分析航班价格走势并给出购买建议。",
                },
                {
                    "role": "user",
                    "content": test_prompt,
                },
            ],
            max_tokens=150,
            temperature=0.7,
        )

        print("✓ API request successful")
        print(f"  Model used: {response.model}")
        print(f"  Tokens used: {response.usage.total_tokens}")

    except Exception as e:
        print(f"✗ API request failed: {e}")
        return False

    # Step 4: Display response
    print("\n[4/4] Displaying LLM response...")
    if response.choices and len(response.choices) > 0:
        content = response.choices[0].message.content
        print("\n" + "-" * 60)
        print("LLM 分析结果:")
        print("-" * 60)
        print(content)
        print("-" * 60)
        return True
    else:
        print("✗ No response content received")
        return False


def main() -> int:
    """Main entry point."""
    try:
        success = asyncio.run(verify_llm())

        if success:
            print_header("VERIFICATION PASSED")
            print("\n✓ LLM integration is working correctly!")
            print("✓ DeepSeek API is accessible and responding.")
            print("\nNext steps:")
            print("  1. Implement price trend analysis logic")
            print("  2. Create structured prompts for flight analysis")
            print("  3. Integrate with the main monitoring system\n")
            return 0
        else:
            print_header("VERIFICATION FAILED")
            print("\n✗ LLM verification failed.")
            print("✗ Please check:")
            print("  1. DEEPSEEK_API_KEY is set correctly")
            print("  2. API key is valid and has credits")
            print("  3. Network connection is available")
            print("  4. DEEPSEEK_BASE_URL is correct (default: https://api.deepseek.com)\n")
            return 1

    except KeyboardInterrupt:
        print("\n\n✗ Verification interrupted by user.\n")
        return 1
    except Exception as e:
        print_header("UNEXPECTED ERROR")
        print(f"\n✗ An unexpected error occurred: {e}\n")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
