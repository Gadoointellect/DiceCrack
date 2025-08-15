# 🎲 DiceCrack

**DiceCrack** is a web-based Dice Result Calculator that finds the correct unhashed server seed from a hashed seed, client seed, and nonce using a wordlist.  
It works with both uploaded and pasted wordlists, supports speed limiting, and displays live progress updates.

---

## 🚀 Features

- Upload `.txt`, `.zip`, or `.gz` wordlists
- Paste wordlist directly into the app
- Live progress tracking (processed count, speed, ETA)
- Pause & resume functionality
- Dark mode toggle
- Result export to `.txt`
- Built-in spinner and progress bar animations

---

## 📂 Project Structure

```bash
dicecrack/
├── backend/ # Flask API backend
│ ├── app.py # Main backend logic
│ ├── requirements.txt
│ └── ...
├── frontend/
│ ├── index.html # Main HTML + CSS + JavaScript
│ └── ...
└── README.md
```
---

## 🛠 Installation (Local)

1. Clone the repository:
   ```bash
   git clone https://github.com/gadoointellect/dicecrack.git
   cd dicecrack
2. Create a Python virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate   # On Windows: venv\Scripts\activate
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
4. Run the backend:
   ```bash
   python backend/app.py
5. Open frontend/index.html in your browser.
