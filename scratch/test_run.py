import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# Add project root to sys.path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from agent.core import run_agent, _ensure_code_change, _tool_calls_modified_files

print("Running agent...")
res = run_agent("4622", "gin-gonic", "gin", "repos/gin")

print("\n--- AGENT RESULT ---")
print("keys:", res.keys())
print("final_response length:", len(res.get("final_response", "")))
print("final_response snippet:")
print(res.get("final_response", "")[:500])
print("\ntool_calls count:", len(res.get("tool_calls", [])))
for i, call in enumerate(res.get("tool_calls", [])):
    print(f"  {i}: {call['name']} -> success: {call.get('result', {}).get('success')}")

print("\nRunning _ensure_code_change check:")
ensured = _ensure_code_change("repos/gin", "4622", res.get("final_response", ""), res.get("tool_calls", []))
print("ensured change result:", ensured)
