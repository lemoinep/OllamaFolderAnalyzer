"""
OLLAMA FOLDER ANALYZER
Dr. Patrick Lemoine | Version: 1.0 | 2026
"""

import os
import sys
import json
import ollama
import sqlite3
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List
from collections import Counter
import json
import csv
from datetime import datetime

import subprocess
import psutil
import requests
from datetime import datetime
import pyttsx3
import platform

__version__ = "1.0"

__banner__ = """
╔══════════════════════════════════════╗
║    🛡️  OLLAMA FOLDER ANALYZER 🛡️     ║
║              Scan v1.                ║
║     for Ollama Local LLMs            ║
╚══════════════════════════════════════╝
"""

print(__banner__)
print(f"Version {__version__} | {platform.system()} | {datetime.now().strftime('%Y-%m-%d %H:%M')}")


OLLAMA_BASE_URL = "http://localhost:11434"
MODEL_NAME = "qwen2.5-coder:7b"
JSON_PATH = "ollama_path.json"

def save_path_to_json(path: str) -> None:
    """Save Ollama executable path to a JSON file."""
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump({"ollama_path": path}, f)


def load_path_from_json() -> str | None:
    """Load Ollama executable path from a JSON file if it exists."""
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("ollama_path")
    return None


def find_ollama_executable() -> str | None:
    """Try to locate Ollama.exe on the system."""
    # Search in PATH
    for path_dir in os.getenv("PATH", "").split(os.pathsep):
        candidate = os.path.join(path_dir, "Ollama.exe")
        if os.path.isfile(candidate):
            return candidate

    # Search typical install folders on Windows
    potential_dirs = [
        r"C:\\Program Files\\Ollama",
        r"C:\\Program Files (x86)\\Ollama",
    ]
    for d in potential_dirs:
        candidate = os.path.join(d, "Ollama.exe")
        if os.path.isfile(candidate):
            return candidate

    return None


def is_ollama_running() -> bool:
    """Check if Ollama server process is already running."""
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] and "Ollama" in proc.info["name"]:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def launch_ollama_if_needed() -> None:
    """Launch Ollama server if not already running."""
    path = load_path_from_json()
    if path is None or not os.path.isfile(path):
        path = find_ollama_executable()
        if path:
            save_path_to_json(path)
        else:
            print("Ollama.exe not found on the system.")
            return

    if not is_ollama_running():
        subprocess.Popen(
            [path, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"Launching Ollama from: {path}")
    else:
        print("Ollama is already running.")


def list_models() -> List[str]:
    """Return the list of available models from the local Ollama server."""
    try:
        url = f"{OLLAMA_BASE_URL}/api/tags"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            return [m["name"] for m in models]
        else:
            print(f"Failed to fetch models: {response.status_code}")
            return []
    except Exception as e:
        print(f"Error connecting to Ollama server: {e}")
        return []


class FolderAnalysis(BaseModel):
    """JSON schema expected from the Ollama analysis."""

    topic: str = Field(..., description="Main topic of the folder")
    summary: str = Field(..., max_length=200, description="Concise summary")
    keywords: List[str] = Field(..., description="Technical keywords")
    confidence: float = Field(..., ge=0, le=1, description="Confidence (0.0-1.0)")
    languages: List[str] = Field(
        default_factory=list, description="Detected programming languages"
    )


IGNORED_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "build",
    "dist",
}


class FolderAnalyzer:
    """Analyze code/project folders using an Ollama model and store results in SQLite."""

    def __init__(self, db_path: str = "analyzed_folders.db", model: str = MODEL_NAME):
        self.db_path = db_path
        self.model = model
        self.init_db()

    def init_db(self) -> None:
        """Create the SQLite schema if it does not exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyzed_folders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    relative_folder TEXT UNIQUE,
                    absolute_path TEXT,
                    file_count INTEGER,
                    topic TEXT,
                    summary TEXT,
                    confidence REAL,
                    analysis_timestamp TEXT,
                    model_used TEXT
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder_id INTEGER,
                    keyword TEXT,
                    FOREIGN KEY(folder_id) REFERENCES analyzed_folders(id)
                )
                """
            )

    def extract_folder_summary(self, folder: Path) -> str:
        """Extract a short textual summary of the folder content."""
        files = [p for p in folder.iterdir() if p.is_file()]
        subdirs = [
            p.name
            for p in folder.iterdir()
            if p.is_dir() and p.name not in IGNORED_DIRS
        ]

        summary_parts = [
            f"Folder: {folder.name}",
            f"Subfolders: {', '.join(subdirs[:10]) or 'none'}",
        ]

        ext_count = Counter(p.suffix.lower() for p in files)
        summary_parts.append(
            "Extensions: "
            + ", ".join(f"{ext}: {count}" for ext, count in ext_count.most_common(5))
        )

        text_files = [
            p
            for p in files
            if p.suffix.lower()
            in {".py", ".cpp", ".cu", ".md", ".txt", ".rst", ".yml", ".json"}
        ]
        for tf in text_files[:3]:
            try:
                content = tf.read_text(encoding="utf-8", errors="ignore")[:1000]
                summary_parts.append(f"{tf.name}: {content[:200]}...")
            except Exception:
                summary_parts.append(f"{tf.name}: (unable to read)")

        return "\n".join(summary_parts)

    def analyze_folder(self, folder: Path) -> dict:
        """Analyze a single folder using the Ollama model."""
        folder_summary = self.extract_folder_summary(folder)
        prompt = f"""Analyze this code/project folder.

Content:
{folder_summary}

Respond ONLY with a valid JSON matching this schema:
{json.dumps(FolderAnalysis.model_json_schema(), indent=2)}
"""
        try:
            response = ollama.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                format=FolderAnalysis.model_json_schema(),
            )

            analysis = FolderAnalysis.model_validate_json(
                response["message"]["content"]
            )

            return {
                "relative_folder": str(folder.relative_to(folder.parent.parent)),
                "absolute_path": str(folder),
                "file_count": len(list(folder.rglob("*"))),
                "analysis": analysis.model_dump(),
            }
        except Exception as e:
            # Fallback in case of error
            return {
                "relative_folder": str(folder.relative_to(folder.parent.parent)),
                "absolute_path": str(folder),
                "file_count": 0,
                "analysis": {
                    "topic": "ERROR",
                    "summary": str(e),
                    "keywords": [],
                    "confidence": 0.0,
                    "languages": [],
                },
            }

    def save_to_db(self, result: dict) -> None:
        """Save a folder analysis into SQLite, updating duplicates."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO analyzed_folders 
                (relative_folder, absolute_path, file_count, topic, summary, confidence, analysis_timestamp, model_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["relative_folder"],
                    result["absolute_path"],
                    result["file_count"],
                    result["analysis"]["topic"],
                    result["analysis"]["summary"],
                    result["analysis"]["confidence"],
                    datetime.now().isoformat(),
                    self.model,
                ),
            )

            folder_id = cursor.lastrowid

            # Insert keywords
            for kw in result["analysis"]["keywords"]:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO keywords (folder_id, keyword) 
                    VALUES (?, ?)
                    """,
                    (folder_id, kw),
                )

    def scan_all_folders(self, root_folder: Path) -> None:
        """Scan and analyze all subfolders under the given root folder."""
        subfolders = [
            p
            for p in root_folder.rglob("*")
            if p.is_dir() and p.name not in IGNORED_DIRS
        ]
        print(f"Analyzing {len(subfolders)} subfolders...")

        for i, folder in enumerate(subfolders):
            print(f"[{i + 1}/{len(subfolders)}] {folder.name}...")
            result = self.analyze_folder(folder)
            self.save_to_db(result)

        print("✅ Saved to SQLite!")

    def export_csv(self, csv_file: str) -> None:
        """Export SQLite data to a CSV file."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT af.*, GROUP_CONCAT(kw.keyword, ', ') AS all_keywords
                FROM analyzed_folders af
                LEFT JOIN keywords kw ON af.id = kw.folder_id
                GROUP BY af.id
                ORDER BY af.confidence DESC
                """
            )

            with open(csv_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "relative_folder",
                        "absolute_path",
                        "file_count",
                        "topic",
                        "summary",
                        "keywords",
                        "confidence",
                        "analysis_timestamp",
                        "model_used",
                    ]
                )
                for row in cursor:
                    # row order:
                    # 0:id, 1:relative_folder, 2:absolute_path, 3:file_count,
                    # 4:topic, 5:summary, 6:confidence, 7:analysis_timestamp,
                    # 8:model_used, 9:all_keywords
                    writer.writerow(
                        [
                            row[1],  # relative_folder
                            row[2],  # absolute_path
                            row[3],  # file_count
                            row[4],  # topic
                            row[5],  # summary
                            row[9],  # all_keywords
                            row[6],  # confidence
                            row[7],  # analysis_timestamp
                            row[8],  # model_used
                        ]
                    )

    def query_topics(self, topic_contains: str | None = None) -> None:
        """Print folders whose topic matches a given substring."""
        with sqlite3.connect(self.db_path) as conn:
            if topic_contains:
                cursor = conn.execute(
                    """
                    SELECT relative_folder, topic, confidence 
                    FROM analyzed_folders 
                    WHERE topic LIKE ? 
                    ORDER BY confidence DESC
                    """,
                    (f"%{topic_contains}%",),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT relative_folder, topic, confidence 
                    FROM analyzed_folders 
                    ORDER BY confidence DESC 
                    LIMIT 10
                    """
                )

            for row in cursor:
                print(f"{row[0]}: {row[1]} (confidence: {row[2]:.1%})")


def main() -> None:
    """Interactive CLI for the folder analyzer."""
    # Launch Ollama if it's not already running
    launch_ollama_if_needed()

    # Check if the model exists locally
    models = list_models()
    if MODEL_NAME not in models:
        print(f"Model {MODEL_NAME} not found locally. Available models: {models}")
        return

    db_path = f"analyzed_folders_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    analyzer = FolderAnalyzer(db_path)

    while True:
        print("\n=== Folder Analyzer with SQLite ===")
        print("1. Scan a root folder")
        print("2. Export to CSV")
        print("3. Search by topic")
        print("4. Show database statistics")
        print("0. Quit")

        choice = input("Choice: ").strip()

        if choice == "1":
            root_folder_str = input("Root folder path: ").strip()
            root_folder = Path(root_folder_str)
            analyzer.scan_all_folders(root_folder)
        elif choice == "2":
            csv_file = (
                input("CSV file name (default: export.csv): ").strip() or "export.csv"
            )
            analyzer.export_csv(csv_file)
            print(f"✅ Exported to {csv_file}")
        elif choice == "3":
            topic = input("Topic to search (e.g. 'CUDA', 'AI'): ").strip()
            analyzer.query_topics(topic)
        elif choice == "4":
            with sqlite3.connect(db_path) as conn:
                total = conn.execute(
                    "SELECT COUNT(*) FROM analyzed_folders"
                ).fetchone()[0]
                print(f"📊 {total} folders analyzed in {db_path}")
        elif choice == "0":
            break
        else:
            print("Invalid choice, please try again.")


if __name__ == "__main__":
    main()
    
