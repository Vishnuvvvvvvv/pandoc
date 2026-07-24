Just one file to copy:

excel_parsing.py
That's it. It's fully self-contained.

In your other FastAPI project, do this:
1. Copy the file

excel_parsing.py  →  your-other-project/excel_parsing.py
2. Mount it in your main.py / app.py

python
from excel_parsing import router as excel_router
app.include_router(excel_router, prefix="/excel", tags=["Excel Pipeline"])
3. Install the two dependencies

bash
uv add openpyxl xlrd
# or with pip:
pip install openpyxl xlrd
4. Add this to your .env (optional)

env
EXCEL_OUTPUT_DIR=./uploads/excel   # where outputs are saved
EXCEL_MAX_ROWS=5000                # safety cap
EXCEL_MAX_COLS=200