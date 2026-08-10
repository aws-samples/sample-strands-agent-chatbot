---
name: code-interpreter
description: Test and prototype code in a sandboxed environment. Use for debugging, verifying logic, or installing packages.
---

# Code Interpreter

A general-purpose code execution environment powered by AWS Bedrock AgentCore Code Interpreter. Run code, execute shell commands, and manage files in a secure sandbox.

## Available Tools

- **execute_code(code, language, output_filename)**: Execute Python, JavaScript, or TypeScript code.
- **execute_command(command)**: Execute shell commands.
- **file_operations(operation, paths, content)**: Read, write, list, or remove files in the sandbox.
- **ci_push_to_workspace(paths)**: Compatibility helper for environments without a mounted workspace. Do not call it when `/mnt/workspace` is available.

## Tool Parameters

### execute_code

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `code` | string | Yes | | Code to execute. Use `print()` for text output. |
| `language` | string | No | `"python"` | `"python"`, `"javascript"`, or `"typescript"` |
| `output_filename` | string | No | `""` | File to download after execution. Code must save a file with this exact name. Saved to workspace automatically. |

### execute_command

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | Yes | Shell command to execute (e.g., `"ls -la"`, `"pip install requests"`). |

### file_operations

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `operation` | string | Yes | `"read"`, `"write"`, `"list"`, or `"remove"` |
| `paths` | list | For read/list/remove | File paths. read: `["file.txt"]`, list: `["."]`, remove: `["old.txt"]` |
| `content` | list | For write | Entries with `path` and `text`: `[{"path": "out.txt", "text": "hello"}]` |

## tool_input Examples

### execute_code — text output

```json
{
  "code": "import pandas as pd\ndf = pd.DataFrame({'A': [1,2,3], 'B': [4,5,6]})\nprint(df.describe())",
  "language": "python"
}
```

### execute_code — generate chart

```json
{
  "code": "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nimport numpy as np\nx = np.linspace(0, 10, 100)\nplt.figure(figsize=(10,6))\nplt.plot(x, np.sin(x))\nplt.title('Sine Wave')\nplt.savefig('sine.png', dpi=300, bbox_inches='tight')\nprint('Done')",
  "language": "python",
  "output_filename": "sine.png"
}
```

### execute_command — install a package

```json
{
  "command": "pip install yfinance"
}
```

### execute_command — check environment

```json
{
  "command": "python --version && pip list | head -20"
}
```

### file_operations — write a file

```json
{
  "operation": "write",
  "content": [{"path": "config.json", "text": "{\"key\": \"value\"}"}]
}
```

### file_operations — list files

```json
{
  "operation": "list",
  "paths": ["."]
}
```

### file_operations — read a file

```json
{
  "operation": "read",
  "paths": ["output.csv"]
}
```

## When to Use This Skill

Use code-interpreter as a **sandbox for testing and prototyping code**.
For production tasks (creating documents, charts, presentations), prefer specialized skills.

**Do NOT use for:**
- Formatting or displaying code examples (respond directly with markdown code blocks)
- Explaining code or algorithms (respond directly with text)
- Simple calculations you can do mentally (just provide the answer)
- Any task that doesn't require actual code execution

| Task | Recommended Skill | Notes |
|------|-------------------|-------|
| Create charts/diagrams | **visual-design** | Use this first for production charts |
| Create Word documents | **word-documents** | Has template support and styling |
| Create Excel spreadsheets | **excel-spreadsheets** | Has formatting pipeline and validation |
| Create PowerPoint | **powerpoint-presentations** | Has layout system and design patterns |
| **Test code snippets** | **code-interpreter** | Debug, verify logic, check output |
| **Prototype algorithms** | **code-interpreter** | Experiment before implementing |
| **Install/test packages** | **code-interpreter** | Check compatibility, test APIs |
| Debug code logic | code-interpreter | Isolate and test specific functions |
| Verify calculations | code-interpreter | Quick math or data checks |

## Code Interpreter vs Code Agent

| | Code Interpreter | Code Agent |
|---|---|---|
| **Nature** | Sandboxed execution environment | Autonomous agent (Claude Code) |
| **Best for** | Quick scripts, data analysis, prototyping | Multi-file projects, refactoring, test suites |
| **File persistence** | Files in `/mnt/workspace` persist across interpreter restarts | All files auto-synced to S3 |
| **Session state** | Variables persist within one interpreter session; workspace files persist for the chat session | Files + conversation persist across sessions |
| **Autonomy** | You write the code | Agent plans, writes, runs, and iterates |
| **Use when** | You need to run a specific piece of code | You need an engineer to solve a problem end-to-end |

## Workspace Integration

The chat session has a persistent filesystem mounted at `/mnt/workspace`.
Relative file paths used by Code Interpreter tools resolve inside this directory.
Files written there are immediately available in the Workspace panel under the
`code-interpreter/` namespace and remain available when the interpreter session
is restarted.

**Create persistent files directly:**

```json
{
  "tool": "execute_code",
  "code": "from pathlib import Path\nPath('/mnt/workspace/results.json').write_text('{\"ok\": true}')"
}
```

`output_filename` may still be used when a generated file should be surfaced as
an explicit artifact. It is not required for persistence. Do not call
`ci_push_to_workspace` when the mount is available; that tool remains only for
legacy fallback deployments.

**Uploaded files:**

Files uploaded by the user are available in the mounted workspace without
manual loading or base64 transfer.

**Read saved files via workspace skill:**
```
workspace_read("code-interpreter/chart.png")
workspace_read("code-interpreter/results.json")
workspace_list("code-interpreter/")
```

Text and binary files use the same mounted filesystem; no transfer step is
required.

## Environment

- **Languages:** Python (recommended, 200+ libraries), JavaScript, TypeScript
- **Shell:** Full shell access via `execute_command`
- **File system:** `/mnt/workspace` persists across Code Interpreter restarts for the chat session
- **Session state:** Variables persist within one interpreter session; files persist in the mounted workspace
- **Network:** Internet access available (can use `requests`, `urllib`, `curl`)

## Supported Languages

- **Python** (recommended) — 200+ pre-installed libraries covering data science, ML, visualization, file processing
- **JavaScript** — Node.js runtime, useful for JSON manipulation, async operations
- **TypeScript** — TypeScript runtime with type checking

## Pre-installed Python Libraries

### Data Analysis & Visualization

| Library | Common Use |
|---------|------------|
| `pandas` | DataFrames, CSV/Excel I/O, groupby, pivot |
| `numpy` | Arrays, linear algebra, random, statistics |
| `matplotlib` | Line, bar, scatter, histogram, subplots |
| `plotly` | Interactive charts, 3D plots |
| `bokeh` | Interactive visualization |
| `scipy` | Optimization, interpolation, signal processing |
| `statsmodels` | Regression, time series, hypothesis tests |
| `sympy` | Algebra, calculus, equation solving |

### Machine Learning & AI

| Library | Common Use |
|---------|------------|
| `scikit-learn` | Classification, regression, clustering, pipelines |
| `torch` / `torchvision` / `torchaudio` | Deep learning, computer vision, audio |
| `xgboost` | High-performance gradient boosting |
| `spacy` / `nltk` / `textblob` | NLP, tokenization, NER, sentiment |
| `scikit-image` | Image processing, filters, segmentation |

### Mathematical & Optimization

| Library | Common Use |
|---------|------------|
| `cvxpy` | Convex optimization, portfolio optimization |
| `ortools` | Scheduling, routing, constraint programming |
| `pulp` | Linear programming |
| `z3-solver` | SAT solving, formal verification |
| `networkx` / `igraph` | Graph algorithms, network analysis |

### File Processing & Documents

| Library | Common Use |
|---------|------------|
| `openpyxl` / `xlrd` / `XlsxWriter` | Excel read/write with formatting |
| `python-docx` | Word document creation/modification |
| `python-pptx` | PowerPoint creation/modification |
| `PyPDF2` / `pdfplumber` / `reportlab` | PDF read/write/generate |
| `lxml` / `beautifulsoup4` | XML/HTML parsing |
| `markitdown` | Convert various formats to Markdown |

### Image & Media

| Library | Common Use |
|---------|------------|
| `pillow` (PIL) | Image resize, crop, filter, conversion |
| `opencv-python` (cv2) | Computer vision, feature detection |
| `imageio` / `moviepy` | Image/video I/O and editing |
| `pydub` | Audio manipulation |
| `svgwrite` / `Wand` | SVG creation, ImageMagick |

### Data Storage & Formats

| Library | Common Use |
|---------|------------|
| `duckdb` | SQL queries on DataFrames and files |
| `SQLAlchemy` | SQL ORM and database abstraction |
| `pyarrow` | Parquet and Arrow format processing |
| `orjson` / `ujson` / `PyYAML` | Fast JSON/YAML parsing |

### Web & API

| Library | Common Use |
|---------|------------|
| `requests` / `httpx` | HTTP requests, API calls |
| `beautifulsoup4` | Web scraping |
| `fastapi` / `Flask` / `Django` | Web frameworks |

### Utilities

| Library | Common Use |
|---------|------------|
| `pydantic` | Data validation, schema definition |
| `Faker` | Test data generation |
| `rich` | Pretty printing, tables |
| `cryptography` | Encryption, hashing |
| `qrcode` | QR code generation |
| `boto3` | AWS SDK |

> For the full list of 200+ libraries with versions, run: `execute_command(command="pip list")`

## Usage Patterns

### Pattern 1: Data Analysis

```python
import pandas as pd
import numpy as np

df = pd.DataFrame({
    'date': pd.date_range('2024-01-01', periods=100),
    'revenue': np.random.normal(1000, 200, 100),
    'costs': np.random.normal(700, 150, 100),
})
df['profit'] = df['revenue'] - df['costs']

print("=== Summary Statistics ===")
print(df.describe())
print(f"\nTotal Profit: ${df['profit'].sum():,.2f}")
print(f"Profit Margin: {df['profit'].mean() / df['revenue'].mean() * 100:.1f}%")
```

### Pattern 2: Visualization (with output_filename)

```python
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

fig, axes = plt.subplots(2, 2, figsize=(14, 10))

categories = ['Q1', 'Q2', 'Q3', 'Q4']
values = [120, 150, 180, 210]
axes[0,0].bar(categories, values, color='#2196F3')
axes[0,0].set_title('Quarterly Revenue')

x = np.linspace(0, 10, 50)
axes[0,1].plot(x, np.sin(x), 'b-', linewidth=2)
axes[0,1].set_title('Trend')

sizes = [35, 30, 20, 15]
axes[1,0].pie(sizes, labels=['A','B','C','D'], autopct='%1.1f%%')
axes[1,0].set_title('Market Share')

x = np.random.normal(50, 10, 200)
y = x * 1.5 + np.random.normal(0, 15, 200)
axes[1,1].scatter(x, y, alpha=0.5, c='#FF5722')
axes[1,1].set_title('Correlation')

plt.tight_layout()
plt.savefig('dashboard.png', dpi=300, bbox_inches='tight')
print('Dashboard saved')
```

### Pattern 3: Machine Learning

```python
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.datasets import load_iris

iris = load_iris()
X_train, X_test, y_train, y_test = train_test_split(
    iris.data, iris.target, test_size=0.3, random_state=42
)

model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
y_pred = model.predict(X_test)

print(classification_report(y_test, y_pred, target_names=iris.target_names))
```

### Pattern 4: SQL with DuckDB

```python
import duckdb
import pandas as pd

orders = pd.DataFrame({
    'order_id': range(1, 101),
    'customer': [f'Customer_{i%20}' for i in range(100)],
    'amount': [round(50 + i * 3.5, 2) for i in range(100)],
})

result = duckdb.sql("""
    SELECT customer, COUNT(*) as cnt, ROUND(SUM(amount), 2) as total
    FROM orders GROUP BY customer
    HAVING COUNT(*) >= 3 ORDER BY total DESC LIMIT 10
""").df()
print(result.to_string(index=False))
```

### Pattern 5: Fetch Data from Web

```python
import requests
import pandas as pd

response = requests.get("https://api.example.com/data")
data = response.json()
df = pd.DataFrame(data)
print(df.head())
```

### Pattern 6: Multi-step Workflow (session state persists)

```
Call 1: execute_code → load and clean data, store in variable `df`
Call 2: execute_code → analyze `df`, generate chart, save as PNG
Call 3: execute_code → export results to CSV
Call 4: file_operations(operation="read") → download the CSV
```

Variables (`df`) and files persist across calls in the same session.

## Important Rules

1. **`matplotlib.use('Agg')` before `import matplotlib.pyplot`** — sandbox has no display.
2. **Use `print()` for text output** — stdout is how results are returned.
3. **`output_filename` must match exactly** — the filename in `plt.savefig()` or `wb.save()` must match the `output_filename` parameter.
4. **Use `execute_command` for shell tasks** — `ls`, `pip install`, `curl`, etc.
5. **Use `file_operations` for file management** — read/write/list/remove files explicitly.
6. **Session state persists** — variables and files remain across calls. Use this for multi-step workflows.

## Common Mistakes to Avoid

- Forgetting `matplotlib.use('Agg')` before `import matplotlib.pyplot as plt`
- Using `plt.show()` instead of `plt.savefig()` — there is no display
- Typo in `output_filename` — must match the file saved by the code exactly
- Using `execute_code` for shell tasks — use `execute_command` instead
- Writing binary files via `file_operations` — use `execute_code` to generate binary files, then download with `output_filename`
