"""
Module 1 - Test Script
Purpose: Confirm that Python can send a prompt to the local AI model (via Ollama)
and get a response back. This proves the "brain" of TaskForge is working,
before we add any perception or execution logic.

How to run:
1. Make sure Ollama is running (it runs automatically in the background after install)
2. Make sure you've already run: ollama pull phi3:mini
3. Activate your virtual environment: venv\\Scripts\\activate
4. Run this file: python module1_test_brain.py
"""

import requests
import json

# This is the local address Ollama listens on. Nothing here ever goes to the internet -
# 127.0.0.1 always means "this same computer", never an external server.
OLLAMA_URL = "http://localhost:11434/api/generate"


def ask_local_ai(prompt: str) -> str:
    """
    Sends a prompt to the local Phi-3-mini model running inside Ollama,
    and returns its text response.
    """
    payload = {
        "model": "phi3:mini",
        "prompt": prompt,
        "stream": False  # False = wait for the full answer at once, easier to work with for now
    }

    response = requests.post(OLLAMA_URL, json=payload)

    if response.status_code != 200:
        raise RuntimeError(f"Ollama returned an error: {response.status_code} - {response.text}")

    data = response.json()
    return data["response"]


if __name__ == "__main__":
    print("Sending a test prompt to your local AI model...\n")

    test_prompt = "In one short sentence, explain what an offline AI agent is."
    answer = ask_local_ai(test_prompt)

    print("AI's response:")
    print(answer)
    print("\nIf you see a sensible sentence above, Module 1 is working correctly.")
