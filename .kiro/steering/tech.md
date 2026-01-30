# Tech Stack

## Backend
- **Framework**: Flask 3.0.3 with Flask-CORS
- **Database**: SQLite via SQLAlchemy 2.0.35
- **Language**: Python 3.x

## Document Processing
- **PDF**: PyMuPDF (fitz) 1.23.25, PyPDF2 3.0.1
- **EPUB**: ebooklib 0.18
- **HTML Parsing**: BeautifulSoup4 4.12.3

## AI/LLM Integration
- **Primary**: Doubao (豆包) via `volcenginesdkarkruntime` SDK
- **Alternative**: DeepSeek via OpenAI-compatible client
- **OpenAI SDK**: openai 1.52.2

## WebSocket/Real-time
- **Async**: websockets 13.1
- **Sync**: websocket-client 1.7.0

## Frontend
- Vanilla HTML/CSS/JavaScript (no framework)
- Static files served by Flask

## Environment
- Configuration via `.env` file (python-dotenv)
- Key environment variables:
  - `DOUBAO_API_KEY` / `ARK_API_KEY`: Doubao API authentication
  - `DOUBAO_API_BASE`: Custom API endpoint (optional)
  - `DEEPSEEK_API_KEY`: DeepSeek API key
  - `DATABASE_URL`: Database connection string (defaults to SQLite)
  - `VOLCENGINE_TTS_*`: TTS service credentials

## Common Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
python app.py

# Run with logging to file
python app.py 2>&1 | tee server_log.txt
```

## API Base URLs
- Doubao: `https://ark.cn-beijing.volces.com/api/v3`
- DeepSeek: `https://api.deepseek.com/v1`

## Models Used
- `doubao-seed-1-6-251015`: Deep thinking model for interpretation generation
- `doubao-seed-1-6-flash-250828`: Fast model for title generation
