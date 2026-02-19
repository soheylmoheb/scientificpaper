#!/usr/bin/env python3
"""
Parallel PDF Analyzer with DeepSeek – Multiple Prompts per Paper
Processes 87 papers quickly using concurrent threads.
"""

import os
import sys
import requests
import PyPDF2
from io import BytesIO
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore
import json

# =============================================================================
# CONFIGURATION – ADJUST AS NEEDED
# =============================================================================
OUTPUT_DIR = "deepseek_output"          # main output folder
MAX_WORKERS = 10                        # number of parallel threads (adjust based on API limits)
RATE_LIMIT = 2                           # seconds between requests (if needed, 0 for no delay)
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# -----------------------------------------------------------------------------
# The 8 demands (prompts) to send for EACH paper
# You can edit these to refine the questions
# -----------------------------------------------------------------------------
DEMANDS = [
    "1. Explain the pricing model proposed in this paper in an understandable way. Include any key assumptions and economic principles.",
    "2. Write down all mathematical formulas used in the model, formatted so they can be copied as plain text (e.g., using LaTeX-like notation or plain text equations).",
    "3. Provide a step‑by‑step algorithm to perform a Monte Carlo simulation of this model.",
    "4. Write the Python code that implements the Monte Carlo simulation described in step 3.",
    "5. Suggest the best machine learning or AI algorithm to predict internet prices based on this model, and justify your choice.",
    "6. Write the Python code for that AI algorithm, including necessary data preprocessing steps.",
    "7. Identify a specific Kaggle dataset (or library) that could be used to train the AI model, and explain how it relates to this pricing model.",
    "8. Summarize the key findings of this paper in one paragraph, focusing on how the model differs from others of its time."
]

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
def get_deepseek_key():
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        sys.exit("ERROR: DEEPSEEK_API_KEY environment variable not set.")
    return key

def choose_input_source():
    print("\nChoose input source:")
    print("  1. Folder of PDFs (upload files to this workspace)")
    print("  2. Mendeley library (requires personal access token)")
    while True:
        choice = input("Enter 1 or 2: ").strip()
        if choice == "1":
            return "folder"
        elif choice == "2":
            return "mendeley"
        else:
            print("Invalid choice. Please enter 1 or 2.")

# ----- Folder functions -----
def get_pdf_folder():
    default = "."
    folder = input(f"Enter folder path (press Enter for current '{default}'): ").strip()
    if not folder:
        folder = default
    if not os.path.isdir(folder):
        sys.exit(f"Folder '{folder}' does not exist.")
    return folder

def find_pdfs_in_folder(folder):
    pdfs = []
    for file in os.listdir(folder):
        if file.lower().endswith(".pdf"):
            pdfs.append(os.path.join(folder, file))
    return pdfs

def extract_text_from_pdf_file(pdf_path):
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text

# ----- Mendeley functions (if needed) -----
def get_mendeley_token():
    token = os.environ.get("MENDELEY_TOKEN")
    if not token:
        sys.exit("ERROR: MENDELEY_TOKEN environment variable not set.")
    return token

def get_mendeley_collections(token):
    url = "https://api.mendeley.com/folders"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()

def choose_mendeley_collection(token):
    collections = get_mendeley_collections(token)
    if not collections:
        print("No collections found. Will search entire library.")
        return None
    print("\nAvailable collections:")
    for idx, col in enumerate(collections, 1):
        print(f"  {idx}. {col['name']}")
    while True:
        choice = input("Enter collection number (or Enter for all): ").strip()
        if choice == "":
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(collections):
                return collections[idx]['id']
        except:
            print("Invalid input.")

def get_mendeley_papers(token, collection_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    papers = []
    page = 1
    limit = 50
    base_url = f"https://api.mendeley.com/folders/{collection_id}/documents" if collection_id else "https://api.mendeley.com/documents"
    while True:
        url = f"{base_url}?limit={limit}&page={page}"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        docs = resp.json()
        if not docs:
            break
        for doc in docs:
            if doc.get("files"):
                file_id = doc["files"][0]["id"]
                file_url = f"https://api.mendeley.com/files/{file_id}"
                file_resp = requests.get(file_url, headers=headers)
                file_resp.raise_for_status()
                file_info = file_resp.json()
                if file_info.get("download_url"):
                    papers.append({
                        "title": doc.get("title", "Untitled"),
                        "download_url": file_info["download_url"]
                    })
        page += 1
    return papers

def download_and_extract_text_from_url(pdf_url):
    resp = requests.get(pdf_url)
    resp.raise_for_status()
    pdf_data = resp.content
    with BytesIO(pdf_data) as f:
        reader = PyPDF2.PdfReader(f)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text

# ----- DeepSeek API call -----
def query_deepseek(api_key, prompt, paper_text, demand_number, paper_title, retries=3):
    """
    Send a single demand prompt plus paper text to DeepSeek.
    Returns response text or None if failed.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    # Combine demand with paper text
    full_prompt = f"Paper title: {paper_title}\n\nDemand {demand_number}:\n{prompt}\n\nPaper content:\n{paper_text[:15000]}"  # Limit to 15k tokens to avoid truncation? Adjust.
    messages = [
        {"role": "system", "content": "You are a research assistant specializing in economic models and internet pricing. Provide detailed, accurate answers."},
        {"role": "user", "content": full_prompt}
    ]
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4000  # Increased for detailed answers
    }
    for attempt in range(retries):
        try:
            resp = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers)
            if resp.status_code == 200:
                result = resp.json()
                return result["choices"][0]["message"]["content"]
            elif resp.status_code == 402:
                print(f"  ❌ Payment required. Check your DeepSeek balance.")
                return None
            else:
                print(f"  ⚠️ Attempt {attempt+1} failed: {resp.status_code}")
                time.sleep(2 ** attempt)  # exponential backoff
        except Exception as e:
            print(f"  ⚠️ Exception: {e}")
            time.sleep(2 ** attempt)
    return None

# ----- Worker function for one paper -----
def process_paper(paper_info, api_key, rate_limiter):
    """
    Process all demands for a single paper.
    paper_info: dict with 'title' and either 'file_path' or 'download_url'
    """
    title = paper_info['title']
    print(f"\n📄 Starting: {title}")

    # Extract text
    try:
        if 'file_path' in paper_info:
            text = extract_text_from_pdf_file(paper_info['file_path'])
        else:
            text = download_and_extract_text_from_url(paper_info['download_url'])
    except Exception as e:
        print(f"  ❌ Failed to extract text: {e}")
        return

    if not text.strip():
        print(f"  ⚠️ Empty text, skipping.")
        return

    # Create output subfolder for this paper (safe filename)
    safe_title = "".join(c for c in title if c.isalnum() or c in " ._-").strip()
    paper_folder = os.path.join(OUTPUT_DIR, safe_title)
    os.makedirs(paper_folder, exist_ok=True)

    # Process each demand
    for idx, demand in enumerate(DEMANDS, 1):
        print(f"  🔍 Demand {idx}...")
        # Apply rate limiting
        with rate_limiter:
            # Optional delay
            if RATE_LIMIT > 0:
                time.sleep(RATE_LIMIT)
            response = query_deepseek(api_key, demand, text, idx, title)
        if response:
            # Save response in a file named by demand number
            out_file = os.path.join(paper_folder, f"demand_{idx:02d}.txt")
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(f"Paper: {title}\n")
                f.write(f"Demand {idx}: {demand}\n")
                f.write("="*80 + "\n")
                f.write(response)
            print(f"    ✅ Demand {idx} saved.")
        else:
            print(f"    ❌ Demand {idx} failed.")

    print(f"✅ Completed: {title}")

# =============================================================================
# MAIN PROGRAM
# =============================================================================
def main():
    print("="*70)
    print("PARALLEL PDF ANALYZER WITH DEEPSEEK – 8 DEMANDS PER PAPER")
    print("="*70)

    # Get API key
    api_key = get_deepseek_key()

    # Choose input source and collect papers
    source = choose_input_source()
    papers = []

    if source == "folder":
        folder = get_pdf_folder()
        pdf_files = find_pdfs_in_folder(folder)
        for pdf in pdf_files:
            papers.append({
                "title": os.path.basename(pdf),
                "file_path": pdf
            })
    else:  # Mendeley
        token = get_mendeley_token()
        collection_id = choose_mendeley_collection(token)
        print("\nFetching papers from Mendeley...")
        raw_papers = get_mendeley_papers(token, collection_id)
        for p in raw_papers:
            papers.append({
                "title": p["title"],
                "download_url": p["download_url"]
            })

    if not papers:
        print("No papers found.")
        return

    print(f"\nTotal papers to process: {len(papers)}")
    print(f"Each paper will be queried with {len(DEMANDS)} demands → {len(papers)*len(DEMANDS)} API calls.")
    proceed = input("Proceed? (y/n): ").strip().lower()
    if proceed != 'y':
        return

    # Create main output dir
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Rate limiter semaphore (controls concurrency)
    rate_limiter = Semaphore(MAX_WORKERS)  # allows up to MAX_WORKERS simultaneous requests

    # Use ThreadPoolExecutor to process papers concurrently
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        for paper in papers:
            future = executor.submit(process_paper, paper, api_key, rate_limiter)
            futures.append(future)

        # Monitor progress (optional)
        for future in as_completed(futures):
            try:
                future.result()  # raises exceptions if any
            except Exception as e:
                print(f"Error in paper processing: {e}")

    print("\n" + "="*70)
    print(f"ALL DONE! Results are in folder: {OUTPUT_DIR}")
    print("Each paper has its own subfolder with demand_01.txt ... demand_08.txt")
    print("="*70)

if __name__ == "__main__":
    main()
