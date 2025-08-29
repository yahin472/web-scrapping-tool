from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from bs4 import BeautifulSoup
import requests
from urllib.parse import urljoin
from PIL import Image
from io import BytesIO
import base64
import subprocess
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secret_key'

# MongoDB setup
client = MongoClient("mongodb://localhost:27017/")
db = client["web_scraper"]
url_collection = db["searched_urls"]

@app.route('/')
def home():
    return render_template('index.html', url='', original_blocks=[], modified_blocks=[], entry_id='')

@app.route('/scrape', methods=['POST'])
def scrape():
    url = request.form['url']

    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')

        blocks = []
        p_count = h_count = img_count = 1
        main = soup.find('main') or soup.find('div', {'id': 'mw-content-text'}) or soup.find('article') or soup.body

        for tag in main.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'img']):
            text = tag.get_text(strip=True)
            if tag.name.startswith('h') and len(text.split()) >= 3:
                blocks.append({'label': f'head{h_count}', 'tag': tag.name, 'content': text})
                h_count += 1
            elif tag.name == 'p' and len(text.split()) >= 5:
                blocks.append({'label': f'para{p_count}', 'tag': 'p', 'content': text})
                p_count += 1
            elif tag.name == 'img' and tag.get('src'):
                img_url = urljoin(url, tag['src'])
                blocks.append({'label': f'img{img_count}', 'tag': 'img', 'src': img_url})
                img_count += 1

        page_title = soup.title.string.strip() if soup.title and soup.title.string else "No Title"
        meta_tag = soup.find('meta', attrs={'name': 'description'})
        meta_desc = meta_tag['content'].strip() if meta_tag and 'content' in meta_tag.attrs else "No Description"
        word_count = sum(len(b.get('content', '').split()) for b in blocks if b['tag'] != 'img')

        existing_entry = url_collection.find_one({"url": url})

        if existing_entry:
            url_collection.update_one(
                {"_id": existing_entry["_id"]},
                {"$set": {
                    "timestamp": datetime.now(),
                    "title": page_title,
                    "description": meta_desc,
                    "word_count": word_count,
                    "paragraphs": p_count - 1,
                    "headers": h_count - 1,
                    "images": img_count - 1,
                    "original_blocks": blocks,
                    "modified_blocks": blocks.copy()
                }}
            )
            entry_id = existing_entry["_id"]
        else:
            inserted = url_collection.insert_one({
                "url": url,
                "timestamp": datetime.now(),
                "title": page_title,
                "description": meta_desc,
                "word_count": word_count,
                "paragraphs": p_count - 1,
                "headers": h_count - 1,
                "images": img_count - 1,
                "original_blocks": blocks,
                "modified_blocks": blocks.copy()
            })
            entry_id = inserted.inserted_id

        return render_template('index.html', url=url, original_blocks=blocks, modified_blocks=blocks, entry_id=str(entry_id))
    except Exception as e:
        return f"<h2>Error scraping site:</h2><pre>{str(e)}</pre>"

@app.route('/transform', methods=['POST'])
def transform():
    data = request.json
    text = data.get('text', '')
    label = data.get('label', '')
    action = data.get('action', '')

    if not text or not action or not label:
        return jsonify({'result': 'Missing parameters'}), 400

    prompt_map = {
        'grammar': f"Correct the grammar: \"{text}\"",
        'rephrase': f"Rephrase this: \"{text}\"",
        'expand': f"Expand this paragraph with more detail: \"{text}\"",
        'tone_professional': f"Rewrite this in a professional tone: \"{text}\"",
        'tone_sad': f"Rewrite this in a sad tone: \"{text}\"",
        'tone_fun': f"Rewrite this in a fun and playful tone: \"{text}\""
    }

    prompt = prompt_map.get(action, f"Perform '{action}' on: {text}")

    try:
        result = subprocess.run(
            ["ollama", "run", "llama3", prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120
        )
        output = result.stdout.decode('utf-8').strip()

        return jsonify({'result': output})
    except Exception as e:
        return jsonify({'result': f"Local LLM error: {str(e)}"}), 500

@app.route('/img2img', methods=['POST'])
def img2img():
    data = request.json
    img_url = data.get('url')
    prompt = data.get('prompt', 'Make this look more artistic')

    try:
        img_response = requests.get(img_url)
        img = Image.open(BytesIO(img_response.content)).convert("RGB")
        img = img.resize((512, 512))

        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        payload = {
            "init_images": [f"data:image/png;base64,{img_base64}"],
            "prompt": prompt,
            "denoising_strength": 0.6,
            "width": 512,
            "height": 512
        }

        r = requests.post("http://127.0.0.1:7860/sdapi/v1/img2img", json=payload)
        result = r.json()

        if 'images' not in result:
            return jsonify({'error': 'No image returned'}), 500

        return jsonify({'image': result['images'][0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/history')
def history():
    entries = list(url_collection.find().sort('_id', -1))
    return render_template('history.html', entries=entries)

@app.route('/delete/<id>', methods=['POST'])
def delete_entry(id):
    try:
        url_collection.delete_one({'_id': ObjectId(id)})
        return redirect(url_for('history'))
    except:
        return "<h3>Failed to delete entry.</h3>"

@app.route('/clear_history', methods=['POST'])
def clear_history():
    try:
        url_collection.delete_many({})
        return redirect(url_for('history'))
    except:
        return "<h3>Failed to clear history.</h3>"

@app.route('/info/<id>')
def info(id):
    try:
        entry = url_collection.find_one({"_id": ObjectId(id)})
        if not entry:
            return "<h3>No info found.</h3>"
        return render_template('info.html', entry=entry)
    except Exception as e:
        return f"<h3>Error loading info page:</h3><pre>{str(e)}</pre>"

@app.route('/save_modifications', methods=['POST'])
def save_modifications():
    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({'status': f'❌ Failed to parse JSON: {str(e)}'}), 400

    blocks = data.get('blocks', {})
    entry_id = data.get('entry_id', '')

    if not blocks:
        return jsonify({'status': '❌ No modifications to save.'}), 400
    if not entry_id:
        return jsonify({'status': '⚠️ No entry ID provided.'}), 400

    try:
        entry = url_collection.find_one({"_id": ObjectId(entry_id)})
        if not entry:
            return jsonify({'status': '❌ Entry not found in DB.'}), 400

        original_blocks = entry.get('original_blocks', [])
        updated_modified_blocks = []
        for orig_block in original_blocks:
            label = orig_block['label']
            if label in blocks:
                new_content = blocks[label]
                if orig_block['tag'] == 'img':
                    updated_modified_blocks.append({'label': label, 'tag': 'img', 'src': new_content})
                else:
                    updated_modified_blocks.append({'label': label, 'tag': orig_block['tag'], 'content': new_content})
            else:
                updated_modified_blocks.append(orig_block)

        url_collection.update_one(
            {"_id": ObjectId(entry_id)},
            {"$set": {"modified_blocks": updated_modified_blocks}}
        )
        return jsonify({'status': '✅ Modifications saved.'})
    except Exception as e:
        return jsonify({'status': f'⚠️ DB update failed: {str(e)}'}), 500

@app.route('/info_detail/<id>')
def info_detail(id):
    try:
        entry = url_collection.find_one({"_id": ObjectId(id)})
        if not entry:
            return "<h3>No info found.</h3>"

        original = entry.get("original_blocks", [])
        modified = entry.get("modified_blocks", [])

        mod_lookup = {b['label']: b for b in modified}
        filtered_modified = []

        for orig in original:
            label = orig['label']
            mod = mod_lookup.get(label)
            if not mod:
                continue
            if orig.get('tag') == 'img':
                if mod.get('src') and mod.get('src') != orig.get('src'):
                    filtered_modified.append(mod)
            else:
                if mod.get('content') and mod.get('content') != orig.get('content'):
                    filtered_modified.append(mod)

        return render_template("info_detail.html", original_blocks=original, modified_blocks=filtered_modified)
    except Exception as e:
        return f"<h3>Error loading detail view:</h3><pre>{str(e)}</pre>"

if __name__ == '__main__':
    app.run(debug=True)
