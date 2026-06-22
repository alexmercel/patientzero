import os
import sys
import google.genai as genai
from google.genai import types

if not os.environ.get("GEMINI_API_KEY"):
    raise RuntimeError("Set GEMINI_API_KEY before running this script.")

client = genai.Client()

file_path = sys.argv[1] if len(sys.argv) > 1 else "voicebot/recordings/recording-0001.mp3"
print("Uploading audio file...")
audio_file = client.files.upload(file=file_path)

prompt = """
Listen to this customer service testing call between an automated medical receptionist and a testing agent acting as a patient.
Analyze the actual audio of the call to learn about the support agent (receptionist) and the testing agent (patient).
Provide insights into:
1. The pacing, latency, and conversational overlap (interruptions) of the responses from both sides.
2. The tone, prosody, and naturalness of the testing agent's voice and delivery. Does it sound robotic or stilted in ways that a transcript wouldn't capture?
3. How the support agent handles interruptions or backchanneling from the testing agent.
4. Specific suggestions for the testing agent's system prompt to make its behavior and voice more natural, dynamic, and robust for finding edge cases.
"""

print("Generating content...")
response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents=[audio_file, prompt]
)

print("\n--- ANALYSIS ---")
print(response.text)
