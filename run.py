"""
ForestGuard — Start server + Huey worker together
Run: python run.py
"""
import subprocess
import sys
import os
import time
import signal

procs = []

def shutdown(sig, frame):
    print("\nShutting down ForestGuard...")
    for p in procs:
        p.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

print("Starting ForestGuard Enterprise...")

# Start FastAPI server
server = subprocess.Popen([
    sys.executable, "-m", "uvicorn",
    "backend.main:app",
    "--host", "0.0.0.0",
    "--port", "8000",
    "--reload"
])
procs.append(server)
print("  FastAPI server started on http://127.0.0.1:8000")

time.sleep(2)

# Start Huey worker for background scans
# ✅ FIX: correct Huey consumer command
worker = subprocess.Popen([
    sys.executable, "-m", "huey.bin.huey_consumer",
    "backend.tasks.huey",
    "-w", "2",           # 2 worker threads
    "-k", "thread"       # use threads (not greenlets)
])
procs.append(worker)
print("  Huey worker started (2 threads)")
print("\nForestGuard is running. Press Ctrl+C to stop.\n")

# Wait for either process to exit
while True:
    for p in procs:
        if p.poll() is not None:
            print(f"Process exited with code {p.returncode}")
            shutdown(None, None)
    time.sleep(1)

test_chat.py
#!/usr/bin/env python3
from backend.rag import ForestGuardGeminiBot
from backend.config import settings

print("Testing Gemini Chatbot...")
print(f"Settings gemini_api_key: {settings.gemini_api_key[:20]}...")

bot = ForestGuardGeminiBot()
print(f"Bot API Key: {bot.api_key[:20]}...")
print(f"Key valid: {bot._has_valid_key}")
print(f"Model: {bot.MODEL}")

response = bot.chat("What is NDVI in simple terms?")
print(f"\nResponse:\n{response}")
