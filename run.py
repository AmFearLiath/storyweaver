import uvicorn
import sys
from pathlib import Path

# Sicherstellen, dass das Projektverzeichnis im Suchpfad ist
sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    print("=" * 60)
    print("  🕸️  Storyweaver — KI Game Master")
    print("=" * 60)
    print("  Backend:   http://localhost:8000")
    print("  Frontend:  http://localhost:8000")
    print("  Ollama:    http://localhost:11434")
    print()
    print("  Öffne http://localhost:8000 im Browser")
    print("=" * 60)
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="warning",
    )
