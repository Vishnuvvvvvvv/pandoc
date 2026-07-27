import os
import time
import requests
from pathlib import Path

# --- CONFIGURATION ---
API_BASE_URL = "http://localhost:8001/pandoc"  # URL where the Pandoc router is hosted
TEST_DOCS_DIR = "test-docs"                    # Folder containing .docx files to test
OUTPUT_DIR = "test-output-md"                  # Folder where the Markdown files will be saved

def test_api():
    input_dir = Path(TEST_DOCS_DIR)
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        print(f"❌ Error: Test directory '{TEST_DOCS_DIR}' not found.")
        return

    test_files = list(input_dir.glob("*.docx")) + list(input_dir.glob("*.doc"))
    test_files = [f for f in test_files if not f.name.startswith("~$")] # Ignore temp files

    if not test_files:
        print(f"⚠️ No test documents found in '{TEST_DOCS_DIR}'.")
        return

    print(f"🚀 Found {len(test_files)} files. Starting API tests...\n")
    
    success_count = 0
    fail_count = 0

    for file_path in test_files:
        print(f"📄 Processing: {file_path.name}")
        
        # 1. Upload the file
        with open(file_path, "rb") as f:
            try:
                response = requests.post(f"{API_BASE_URL}/upload", files={"file": f})
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                print(f"   ❌ Upload failed: {e}")
                fail_count += 1
                continue
        
        job_data = response.json()
        job_id = job_data.get("job_id")
        print(f"   ✅ Uploaded! Job ID: {job_id}. Waiting for processing...")

        # 2. Poll the status
        status = "queued"
        attempts = 0
        while status in ("queued", "processing") and attempts < 30: # 30 attempts * 2s = 60s timeout
            time.sleep(2)
            attempts += 1
            status_resp = requests.get(f"{API_BASE_URL}/status/{job_id}")
            if status_resp.status_code == 200:
                status = status_resp.json().get("status")
            else:
                status = "error"
                break

        if status == "done":
            # 3. Download the Markdown
            md_resp = requests.get(f"{API_BASE_URL}/download/{job_id}/markdown")
            if md_resp.status_code == 200:
                out_file = out_dir / f"{file_path.stem}.md"
                with open(out_file, "wb") as md_f:
                    md_f.write(md_resp.content)
                print(f"   🎉 Success! Markdown saved to: {out_file}")
                success_count += 1
            else:
                print(f"   ❌ Failed to download Markdown. HTTP {md_resp.status_code}")
                fail_count += 1
        else:
            print(f"   ❌ Extraction failed or timed out (Status: {status})")
            fail_count += 1
            
        print("-" * 50)

    print("\n📊 --- TEST SUMMARY ---")
    print(f"Total files: {len(test_files)}")
    print(f"✅ Success:   {success_count}")
    print(f"❌ Failed:    {fail_count}")

if __name__ == "__main__":
    test_api()
