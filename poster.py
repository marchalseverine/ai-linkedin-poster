import os
import json
import time
import re
import subprocess
import feedparser
import requests
import anthropic
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
CLAUDE_API_KEY        = os.environ['CLAUDE_API_KEY']
GEMINI_API_KEY        = os.environ['GEMINI_API_KEY']
TELEGRAM_BOT_TOKEN    = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID      = os.environ['TELEGRAM_CHAT_ID']
LINKEDIN_ACCESS_TOKEN = os.environ['LINKEDIN_ACCESS_TOKEN']

LINKEDIN_VERSION = "202503"

RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/ai/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://thenextweb.com/neural/feed/",
]

# ── 1. Fetch news ─────────────────────────────────────────────────────────────
def fetch_news():
    articles = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                articles.append({
                    'title':   entry.get('title', ''),
                    'summary': entry.get('summary', '')[:500],
                    'url':     entry.get('link', ''),
                })
        except Exception as e:
            print(f"  Feed error ({feed_url}): {e}")
    return articles[:15]

# ── 2. Score all articles ─────────────────────────────────────────────────────
def score_all_news(articles):
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    articles_text = "\n".join(
        [f"{i}. {a['title']}\n{a['summary'][:150]}" for i, a in enumerate(articles)]
    )

    prompt = f"""Tu es expert en contenu LinkedIn tech. Voici {len(articles)} articles AI récents.

{articles_text}

Score chaque article de 0 à 10 pour son potentiel LinkedIn (accessibilité, impact, engagement, originalité).

Réponds UNIQUEMENT avec ce JSON valide (sans markdown) :
[{{"index": 0, "score": 8.5}}, {{"index": 1, "score": 7.0}}, ...]

Inclure les {len(articles)} articles. Index entre 0 et {len(articles)-1}."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    text = re.sub(r'```(?:json)?\s*', '', text).strip('`').strip()
    scores = json.loads(text)

    result = []
    for s in scores:
        idx = s.get('index', -1)
        if 0 <= idx < len(articles):
            result.append((articles[idx], float(s['score'])))

    result.sort(key=lambda x: x[1], reverse=True)
    return result

# ── 3. Send topic selection to Telegram ──────────────────────────────────────
def send_topic_selection(available_scored):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    date_str = datetime.now().strftime("%d/%m/%Y")

    top = available_scored[:10]
    lines = [f"📰 AI News — {date_str}\n\nChoisis ton sujet :\n"]
    for i, (article, score) in enumerate(top):
        dot = "🟢" if score >= 8 else "🟡" if score >= 6 else "🔴"
        title = article['title'][:65] + ("…" if len(article['title']) > 65 else "")
        summary = article['summary'][:100].rstrip() + "…"
        lines.append(f"{i + 1}. {dot} {score:.0f}/10 — {title}\n{summary}\n")

    text = "\n".join(lines)

    buttons = [{"text": str(i + 1), "callback_data": f"topic_{i}"} for i in range(len(top))]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    rows.append([{"text": "❌ Annuler", "callback_data": "ignore"}])

    requests.post(f"{base_url}/sendMessage", json={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'reply_markup': {'inline_keyboard': rows}
    })

# ── 4. Wait for topic choice ──────────────────────────────────────────────────
def wait_for_choice(n_choices, timeout=3600, offset=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    last_update_id = offset
    start_time = time.time()

    while time.time() - start_time < timeout:
        params = {'timeout': 30, 'allowed_updates': ['callback_query', 'message']}
        if last_update_id is not None:
            params['offset'] = last_update_id + 1

        try:
            resp = requests.get(f"{base_url}/getUpdates", params=params, timeout=35)
            updates = resp.json().get('result', [])

            for update in updates:
                last_update_id = update['update_id']

                if 'callback_query' in update:
                    cb = update['callback_query']
                    requests.post(f"{base_url}/answerCallbackQuery", json={
                        'callback_query_id': cb['id'], 'text': '✅'
                    })
                    data = cb['data']
                    if data == 'ignore':
                        return None, last_update_id
                    if data.startswith('topic_'):
                        idx = int(data.split('_')[1])
                        if 0 <= idx < n_choices:
                            return idx, last_update_id

                elif 'message' in update:
                    txt = update['message'].get('text', '').strip().lower()
                    if txt in ('run', '/run'):
                        return 'run', last_update_id

        except Exception as e:
            print(f"  Polling error: {e}")
            time.sleep(5)

    return None, last_update_id

# ── 5. Generate posts EN / FR / ES ────────────────────────────────────────────
def generate_posts(article, attempt=1):
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    alternate_instruction = ""
    if attempt > 1:
        alternate_instruction = (
            "\nIMPORTANT : Génère une VERSION COMPLÈTEMENT DIFFÉRENTE. "
            "Change l'angle, le hook, le ton et la structure. Sois créative et surprenante.\n"
        )

    prompt = f"""Tu es une experte en IA qui écrit des posts LinkedIn de vulgarisation pour un public mixte (tech + non-tech).
Les posts doivent sonner naturels et humains, PAS comme un texte généré par une IA.
IMPORTANT : L'auteure est une femme (Sévi). Utilise les accords féminins en français et en espagnol (ex : convaincue, prête, experte, enthousiaste, etc.).
{alternate_instruction}
Article source :
Titre : {article['title']}
Résumé : {article['summary']}
URL : {article['url']}

Génère 3 posts LinkedIn adaptés culturellement (PAS une traduction littérale) :
- EN : Anglais, audience internationale, ton conversationnel et direct
- FR : Français, ton direct et personnel, style proche d'un post humain
- ES : Espagnol, "tú" naturel, réseau Valencia/Madrid

Règles strictes d'écriture :
- PAS de tiret long (—) ni de tiret moyen (–), utilise des virgules ou des points
- PAS de formulations corporate ni de signature
- PAS de liens dans le post (source ajoutée en commentaire automatiquement)
- Hook percutant sur 1-2 lignes (pas de "Voici" ou "Découvrez")
- 5-7 lignes de corps (vulgarisation + point de vue personnel)
- 1 question finale pour engager les commentaires
- 2-3 hashtags max en fin de post

Génère aussi 1 prompt d'image par langue (sujet visuel concret adapté au contexte de l'article, style photographique éditorial).

Réponds UNIQUEMENT avec ce JSON valide (sans markdown, sans backticks) :
{{
  "en": "texte complet du post en anglais",
  "fr": "texte complet du post en français",
  "es": "texte complet du post en espagnol",
  "image_prompts": {{
    "en": "visual subject for the image, adapted to article context",
    "fr": "visual subject for the image, adapted to article context",
    "es": "visual subject for the image, adapted to article context"
  }}
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    text = re.sub(r'```(?:json)?\s*', '', text).strip('`').strip()
    result = json.loads(text)

    for lang in ['en', 'fr', 'es']:
        if lang in result:
            result[lang] = clean_post_text(result[lang])

    return result

# ── 5b. Clean AI-typical characters ──────────────────────────────────────────
def clean_post_text(text):
    text = text.replace(' — ', ', ').replace('—', ', ')
    text = text.replace(' – ', ', ').replace('–', '-')
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    text = text.replace('\u2026', '...')
    return text.strip()

# ── 6. Generate image ─────────────────────────────────────────────────────────
def generate_image(prompt_text, lang):
    full_prompt = (
        f"Editorial photography style, moody studio shot. {prompt_text}. "
        "Soft coral-red light (#FF6B6B) as the only accent, casting a single side glow. "
        "Deep black background (#1A1A1A), crisp white highlights. "
        "Shot on medium format camera, shallow depth of field. "
        "No text, no logos, no screens with readable content. Clean minimal composition."
    )

    try:
        from google import genai
        from google.genai import types as gtypes
        client = genai.Client(api_key=GEMINI_API_KEY)
    except ImportError:
        print("    ❌ google-genai non installé")
        return None

    flash_models = [
        "gemini-2.5-flash-image",
        "gemini-3.1-flash-image-preview",
    ]

    for model_name in flash_models:
        for retry in range(3):
            try:
                print(f"  🎨 {model_name} [{lang}] (essai {retry + 1})...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=full_prompt,
                    config=gtypes.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"]
                    )
                )
                if not response.candidates or not response.candidates[0].content.parts:
                    print(f"    {model_name} : réponse vide")
                    break
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        img_bytes = part.inline_data.data
                        print(f"    ✅ Image ({model_name}) générée ({len(img_bytes) // 1024}KB)")
                        return img_bytes
                print(f"    {model_name} : pas d'image dans les parts")
                break
            except Exception as e:
                err = str(e)
                if '429' in err or 'quota' in err.lower() or 'rate' in err.lower():
                    wait = 20 * (retry + 1)
                    print(f"    Rate limit — retry dans {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"    {model_name} échec: {e}")
                    break

    try:
        print(f"  🎨 Tentative imagen-4.0-generate-001 pour {lang}...")
        response = client.models.generate_images(
            model="imagen-4.0-generate-001",
            prompt=full_prompt,
            config=gtypes.GenerateImagesConfig(number_of_images=1, aspect_ratio="1:1")
        )
        if response.generated_images:
            img_bytes = response.generated_images[0].image.image_bytes
            if img_bytes:
                print(f"    ✅ Image Imagen 4 générée ({len(img_bytes) // 1024}KB)")
                return img_bytes
    except Exception as e:
        print(f"    Imagen 4 échec: {e}")

    print(f"    ⚠️ Tous les modèles ont échoué pour {lang}")
    return None

# ── 7. Generate all 3 images ──────────────────────────────────────────────────
def generate_images_for_posts(posts):
    images = {}
    image_prompts = posts.get('image_prompts', {})
    for i, lang in enumerate(['en', 'fr', 'es']):
        if i > 0:
            time.sleep(10)
        prompt = image_prompts.get(lang, f"Modern AI technology concept for {lang} audience")
        img = generate_image(prompt, lang)
        images[lang] = img
        if img:
            print(f"  ✅ Image {lang.upper()} générée ({len(img)} bytes)")
        else:
            print(f"  ⚠️  Image {lang.upper()} ignorée")
    return images

# ── 8. Send Telegram preview ──────────────────────────────────────────────────
def send_telegram_preview(posts, article, images, score):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    for lang_code, lang_label in [('en', '🇬🇧 EN'), ('fr', '🇫🇷 FR'), ('es', '🇪🇸 ES')]:
        post_text = posts.get(lang_code, '')
        img_bytes = images.get(lang_code)
        caption = f"{lang_label}\n\n{post_text[:1024]}"

        if img_bytes:
            try:
                resp = requests.post(
                    f"{base_url}/sendPhoto",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'caption': caption},
                    files={'photo': ('image.png', img_bytes, 'image/png')},
                    timeout=30
                )
                if resp.ok:
                    print(f"  ✅ Preview {lang_label} envoyé")
                    continue
            except Exception as e:
                print(f"  ⚠️  sendPhoto échec {lang_code}: {e}")

        requests.post(f"{base_url}/sendMessage", json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': f"⚠️ Sans image\n\n{caption}"
        })

    requests.post(f"{base_url}/sendMessage", json={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': (
            f"📰 {article['title']}\n"
            f"⭐ Score : {score}/10\n\n"
            f"👇 Que veux-tu faire ?"
        ),
        'reply_markup': {
            'inline_keyboard': [
                [
                    {"text": "✅ EN", "callback_data": "publish_en"},
                    {"text": "✅ FR", "callback_data": "publish_fr"},
                    {"text": "✅ ES", "callback_data": "publish_es"},
                ],
                [{"text": "🌍 TOUT publier", "callback_data": "publish_all"}],
                [
                    {"text": "🔄 Autre version", "callback_data": "regenerate"},
                    {"text": "🎲 Autre sujet", "callback_data": "new_topic"},
                ],
                [{"text": "❌ Annuler", "callback_data": "ignore"}],
            ]
        }
    })

# ── 9. Wait for validation decision ──────────────────────────────────────────
def wait_for_validation(timeout=2700, offset=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    last_update_id = offset
    start_time = time.time()

    while time.time() - start_time < timeout:
        params = {'timeout': 30, 'allowed_updates': ['callback_query']}
        if last_update_id is not None:
            params['offset'] = last_update_id + 1

        try:
            resp = requests.get(f"{base_url}/getUpdates", params=params, timeout=35)
            updates = resp.json().get('result', [])

            for update in updates:
                last_update_id = update['update_id']
                if 'callback_query' in update:
                    cb = update['callback_query']
                    requests.post(f"{base_url}/answerCallbackQuery", json={
                        'callback_query_id': cb['id'], 'text': '✅ Reçu !'
                    })
                    return cb['data'], last_update_id

        except Exception as e:
            print(f"  Polling error: {e}")
            time.sleep(5)

    return 'ignore', last_update_id

# ── 10. Get LinkedIn member URN ───────────────────────────────────────────────
def get_linkedin_urn():
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'X-Restli-Protocol-Version': '2.0.0',
    }
    resp = requests.get('https://api.linkedin.com/v2/userinfo', headers=headers)
    print(f"  UserInfo status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        sub = data.get('sub', '')
        print(f"  ✅ LinkedIn : {data.get('name', '?')} ({sub})")
        return sub if sub.startswith('urn:li:person:') else f"urn:li:person:{sub}"
    print(f"  ❌ UserInfo error: {resp.text}")
    return None

# ── 11. Upload image to LinkedIn ──────────────────────────────────────────────
def upload_image_linkedin(img_bytes, author_urn):
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'X-Restli-Protocol-Version': '2.0.0',
        'Content-Type': 'application/json',
    }
    resp = requests.post(
        'https://api.linkedin.com/rest/images?action=initializeUpload',
        headers=headers,
        json={"initializeUploadRequest": {"owner": author_urn}}
    )
    if resp.status_code != 200:
        print(f"  ❌ Init upload error: {resp.text}")
        return None

    data = resp.json()
    upload_url = data.get('value', {}).get('uploadUrl', '')
    image_urn = data.get('value', {}).get('image', '')
    if not upload_url or not image_urn:
        return None

    upload_resp = requests.put(
        upload_url,
        headers={
            'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
            'Content-Type': 'application/octet-stream',
        },
        data=img_bytes
    )
    if upload_resp.status_code not in [200, 201]:
        print(f"  ❌ Upload error: {upload_resp.text}")
        return None

    return image_urn

# ── 12. Publish post on LinkedIn ──────────────────────────────────────────────
def publish_linkedin(text, author_urn, image_urn=None):
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'X-Restli-Protocol-Version': '2.0.0',
        'Content-Type': 'application/json',
    }
    post_data = {
        "author": author_urn,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": []
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if image_urn:
        post_data["content"] = {"media": {"id": image_urn}}

    resp = requests.post('https://api.linkedin.com/rest/posts', headers=headers, json=post_data)
    print(f"  LinkedIn status: {resp.status_code}")

    if resp.status_code == 201:
        post_id = (
            resp.headers.get('x-restli-id')
            or resp.headers.get('X-RestLi-Id')
            or resp.headers.get('X-Restli-Id')
        )
        print(f"  ✅ Post publié (ID: {post_id})")
        return post_id

    print(f"  ❌ Erreur LinkedIn: {resp.text}")
    return None

# ── 13. Post source comment ───────────────────────────────────────────────────
def post_source_comment(post_id, source_url, author_urn):
    if not post_id:
        return
    time.sleep(30)
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'X-Restli-Protocol-Version': '2.0.0',
        'Content-Type': 'application/json',
    }
    resp = requests.post(
        f'https://api.linkedin.com/rest/socialActions/{post_id}/comments',
        headers=headers,
        json={"actor": author_urn, "message": {"text": f"📎 Source : {source_url}"}}
    )
    print(f"  Comment status: {resp.status_code}")

# ── 14. Publish selected languages ───────────────────────────────────────────
def publish_langs(langs, posts, images, article, author_urn):
    for lang in langs:
        print(f"🚀 Publication {lang.upper()}...")
        image_urn = None
        if images.get(lang):
            image_urn = upload_image_linkedin(images[lang], author_urn)
        post_id = publish_linkedin(posts.get(lang, ''), author_urn, image_urn)
        if post_id:
            post_source_comment(post_id, article['url'], author_urn)
            notify_telegram(f"✅ Post {lang.upper()} publié sur LinkedIn !")
        else:
            notify_telegram(f"❌ Erreur publication {lang.upper()}.")

# ── 15. Send restart offer ────────────────────────────────────────────────────
def send_restart_offer():
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': "Envie d'un autre post aujourd'hui ?\n\n(tu peux aussi écrire \"run\" dans ce chat)",
            'reply_markup': {
                'inline_keyboard': [
                    [{"text": "🚀 Nouveau post", "callback_data": "restart"}],
                    [{"text": "👋 Terminer", "callback_data": "done"}],
                ]
            }
        }
    )

# ── 16. Wait for restart signal ───────────────────────────────────────────────
def wait_for_restart(timeout=1800, offset=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    last_update_id = offset
    start_time = time.time()

    while time.time() - start_time < timeout:
        params = {'timeout': 30, 'allowed_updates': ['callback_query', 'message']}
        if last_update_id is not None:
            params['offset'] = last_update_id + 1

        try:
            resp = requests.get(f"{base_url}/getUpdates", params=params, timeout=35)
            updates = resp.json().get('result', [])

            for update in updates:
                last_update_id = update['update_id']

                if 'callback_query' in update:
                    cb = update['callback_query']
                    requests.post(f"{base_url}/answerCallbackQuery", json={
                        'callback_query_id': cb['id'], 'text': '✅'
                    })
                    data = cb['data']
                    if data == 'restart':
                        return True, last_update_id
                    if data == 'done':
                        return False, last_update_id

                elif 'message' in update:
                    txt = update['message'].get('text', '').strip().lower()
                    if txt in ('run', '/run'):
                        return True, last_update_id

        except Exception as e:
            print(f"  Polling error: {e}")
            time.sleep(5)

    return False, last_update_id

# ── Helpers ───────────────────────────────────────────────────────────────────
def notify_telegram(message):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={'chat_id': TELEGRAM_CHAT_ID, 'text': message}
    )


def save_history(article, decision, langs):
    history_file = "history.json"
    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = []

    history.append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "title": article['title'],
        "url": article['url'],
        "decision": decision,
        "langs": langs,
    })

    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    try:
        subprocess.run(["git", "config", "user.email", "action@github.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "GitHub Action"], check=True, capture_output=True)
        subprocess.run(["git", "add", history_file], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"history: {decision} — {article['title'][:60]}"], check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
        print("  ✅ Historique sauvegardé")
    except Exception as e:
        print(f"  ⚠️ Historique non commité: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("🤖 AI LINKEDIN POSTER — Démarrage")
    print("=" * 50)

    telegram_offset = None
    first_run = True

    while True:  # session loop — supports restart within same run

        # ── Phase 1 : Fetch & score ────────────────────────────────────────────
        if not first_run:
            notify_telegram("⏳ Recherche des dernières news AI...")
        first_run = False

        print("📡 Recherche des news AI...")
        articles = fetch_news()
        print(f"✅ {len(articles)} articles trouvés")

        print("🧠 Scoring de tous les articles avec Claude...")
        scored = score_all_news(articles)
        print(f"✅ {len(scored)} articles scorés")

        if not scored:
            notify_telegram("⚠️ Aucun article trouvé aujourd'hui.")
            break

        used_indices = set()

        # ── Phase 2 : Topic selection loop ────────────────────────────────────
        while True:
            available = [(i, a, s) for i, (a, s) in enumerate(scored) if i not in used_indices]
            if not available:
                notify_telegram("❌ Plus d'articles disponibles pour aujourd'hui.")
                break

            send_topic_selection([(a, s) for _, a, s in available])
            print("📱 Liste des sujets envoyée — en attente de ton choix...")

            choice, telegram_offset = wait_for_choice(len(available), offset=telegram_offset)

            if choice is None:
                notify_telegram("👋 À bientôt !")
                return

            if choice == 'run':
                break  # user wants fresh news → restart outer loop

            original_idx, article, score = available[choice]
            used_indices.add(original_idx)

            attempt = 1
            decision = None

            # ── Phase 3 : Generate → Preview → Decision ────────────────────────
            while True:
                print(f"✍️  Génération des posts (tentative {attempt})...")
                posts = generate_posts(article, attempt)

                print("🎨 Génération des images...")
                images = generate_images_for_posts(posts)

                print("📱 Envoi du preview sur Telegram...")
                send_telegram_preview(posts, article, images, score)

                decision, telegram_offset = wait_for_validation(offset=telegram_offset)
                print(f"✅ Décision : {decision}")

                if decision == 'regenerate':
                    attempt += 1
                    notify_telegram(f"🔄 Nouvelle version en cours (tentative {attempt})...")
                    continue

                break  # any other decision exits the generate loop

            if decision == 'new_topic':
                notify_telegram("🎲 Retour au choix des sujets...")
                continue  # back to topic selection (inner while)

            if decision == 'ignore':
                save_history(article, 'ignored', [])
                notify_telegram("❌ Post annulé.")

            elif decision in ('publish_all', 'publish_en', 'publish_fr', 'publish_es'):
                langs_map = {
                    'publish_all': ['en', 'fr', 'es'],
                    'publish_en': ['en'],
                    'publish_fr': ['fr'],
                    'publish_es': ['es'],
                }
                langs_to_publish = langs_map[decision]

                print("🔑 Identification du compte LinkedIn...")
                author_urn = get_linkedin_urn()
                if not author_urn:
                    notify_telegram("❌ Impossible de récupérer le profil LinkedIn. Vérifie le token.")
                    return

                publish_langs(langs_to_publish, posts, images, article, author_urn)
                save_history(article, decision, langs_to_publish)

            # ── Phase 4 : Restart offer ────────────────────────────────────────
            send_restart_offer()
            print("⏳ En attente de décision de relance (30 min max)...")
            should_restart, telegram_offset = wait_for_restart(offset=telegram_offset)

            if should_restart:
                print("🔄 Relance demandée — nouveau cycle")
                break  # break topic loop → outer while restarts with fresh news
            else:
                notify_telegram("👋 Super, à bientôt !")
                print("=" * 50)
                print("✅ SESSION TERMINÉE")
                print("=" * 50)
                return

        # Continue outer while → re-fetch news and restart


if __name__ == "__main__":
    main()
