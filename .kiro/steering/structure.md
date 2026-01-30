# Project Structure

```
/
├── app.py                    # Main Flask application (all routes and business logic)
├── requirements.txt          # Python dependencies
├── protocols/                # WebSocket protocol handling for TTS
│   ├── __init__.py
│   └── protocols.py          # Binary message protocol for Volcengine TTS
│
├── index.html                # Homepage - navigation hub
├── aigen_test_page.html      # Personalized interpretation generation UI
├── book_restructure_page.html # Book/article restructuring UI
├── parser_test_page.html     # Document parsing test UI
├── admin.html                # Chapter data management
├── admin_books.html          # Book management
├── test_doubao_thinking.html # Doubao deep thinking test page
├── test_thinking.html        # Reasoning content extraction test
├── api_documentation.html    # API documentation viewer
│
├── test_*.py                 # Various test scripts
├── *.md                      # Documentation files (troubleshooting, setup guides)
└── .kiro/steering/           # AI assistant steering rules
```

## Architecture Pattern
- **Monolithic Flask app**: Single `app.py` contains all routes, business logic, and database operations
- **Factory pattern**: `create_app()` function initializes Flask app with all configurations
- **Static file serving**: HTML pages served directly by Flask from root directory

## Key Code Organization in app.py
1. **Imports and SDK detection** (lines 1-50)
2. **App factory and database setup** (lines 50-300)
3. **Helper functions**: Text processing, translation, JSON fixing
4. **Document parsing**: PDF/EPUB extraction and TOC cleaning
5. **LLM integration**: Doubao/DeepSeek API calls with fallback
6. **API routes**: REST endpoints for all features
7. **Static page routes**: HTML file serving

## Database Schema (SQLite)
- `settings`: Key-value store for configuration (prompts, API keys)
- `books`: Book metadata (filename, chapter count, word count)
- `chapter_summaries`: Chapter content with translations and summaries
- `interpretations`: Generated interpretation results

## API Route Patterns
- `/api/parse/*`: Document parsing endpoints
- `/api/generate/*`: Content generation endpoints
- `/api/settings/*`: Configuration endpoints
- `/api/admin/*`: Management endpoints
- `/api/restructure/*`: Article splitting endpoints
- `/api/translate/*`: Translation endpoints
- `/api/podcast/*`: TTS/podcast endpoints (experimental)
- `/api/test/*`: Testing endpoints
