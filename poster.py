import os
import json
import time
import re
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

# ── Safe JSON parser ──────────────────────────────────────────────────────────
def safe_json_parse(text):
    """Parse JSON robustement avec 3 stratégies de fallback."""
    # Nettoyage : retire les balises markdown
    text = re.sub(r'```(?:json)?\s*', '', text).strip('`').strip()

    # Stratégie 1 : parse direct
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Stratégie 2 : extraire le bloc JSON avec regex (ignore le texte autour)
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Stratégie 3 : remplacer les vrais sauts de ligne DANS les strings JSON
    try:
        def fix_string_newlines(m):
            return m.group(0).replace('\n', '\\n').replace('\r', '\\r')
        fixed = re.sub(r'"((?:[^"\\]|\\.)*)"', fix_string_newlines, text)
        return json.loads(fixed)
    except (json.JSONDecodeError, re.error):
        pass

    raise ValueError(f"Impossible de parser le JSON après 3 tentatives. Début: {text[:300]}")

# ── 2. Scoring et sélection (Claude) ──────────────────────────────────────────
def score_news(articles):
    print(f"🧠 Scoring de {len(articles)} articles avec Claude...")
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    articles_list = ""
    for i, a in enumerate(articles):
        articles_list += f"[{i}] {a['title']}\n"

    prompt = f"""Tu es un expert en curation de contenu IA pour LinkedIn.
Voici les news des dernières 24h :
{articles_list}

Tâche :
1. Choisis l'article avec le plus gros potentiel de 'hype' et d'engagement.
2. Note-le sur 10.
3. Donne l'index choisi.

Réponds UNIQUEMENT avec ce format JSON (sans markdown, sans backticks) :
{{
  "index": 0,
  "score": 9,
  "reason": "Explication courte"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    result = safe_json_parse(response.content[0].text)
    selected = articles[result['index']]
    print(f"✅ Sélectionné : {selected['title']} (Score: {result['score']}/10)")
    return selected, result['score']

# ── 3. Génération des contenus multilingues ───────────────────────────────────
def generate_posts(article):
    print("✍️ Génération des posts (EN, FR, ES) et des prompts image...")
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    prompt = f"""À partir de cette news : {article['title']} ({article['url']})
Génère 3 posts LinkedIn percutants en Anglais, Français et Espagnol.
Utilise des accroches fortes, des emojis, et des hashtags.

Génère aussi 1 prompt d'image spécifique pour CHAQUE langue (en anglais, pour Gemini).
L'image doit illustrer le sujet de l'article de façon concrète, style photorealistic cinematic, sans texte.

RÈGLE JSON CRITIQUE : représente TOUS les sauts de ligne avec \\n (backslash-n).
Ne mets JAMAIS de vrais retours à la ligne à l'intérieur d'une valeur string JSON.

Réponds UNIQUEMENT en JSON valide (sans markdown, sans backticks) :
{{
  "en": "post complet en anglais",
  "fr": "post complet en français",
  "es": "post complet en espagnol",
  "image_prompts": {{
    "en": "detailed photorealistic scene related to the article, cinematic lighting, no text",
    "fr": "detailed photorealistic scene related to the article, elegant style, cinematic lighting, no text",
    "es": "detailed photorealistic scene related to the article, warm style, cinematic lighting, no text"
  }}
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return safe_json_parse(response.content[0].text)

# ── 4. Génération d'image (Gemini 2.5 Flash Image = Nano Banana) ──────────────
def generate_image(prompt_text, lang):
    """
    Génère une image via Gemini 2.5 Flash Image (Nano Banana).
    Docs : https://ai.google.dev/gemini-api/docs/image-generation
    """
    full_prompt = (
        f"{prompt_text}. "
        "Photorealistic, cinematic lighting, professional tech photography. "
        "High quality, sharp focus, dramatic atmosphere. "
        "No text, no words, no letters, no logos anywhere in the image."
    )

    try:
        from google import genai
        from google.genai import types as gtypes
        client_g = genai.Client(api_key=GEMINI_API_KEY)
    except ImportError:
        print("    ❌ google-genai non installé — pip install google-genai")
        client_g = None

    # ── Tentative 1 : gemini-2.5-flash-image (modèle GA officiel "Nano Banana") ──
    if client_g:
        try:
            print(f"  🎨 Tentative gemini-2.5-flash-image pour {lang}...")
            response = client_g.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=full_prompt,
                config=gtypes.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=gtypes.ImageConfig(aspect_ratio="1:1"),
                )
            )
            # Parcours response.parts (API google-genai >= 1.0)
            for part in response.parts:
                if part.inline_data is not None:
                    img_bytes = part.inline_data.data
                    print(f"    ✅ Image Nano Banana (gemini-2.5-flash-image) générée ({len(img_bytes)//1024}KB)")
                    return img_bytes
            print("    gemini-2.5-flash-image : pas d'image dans response.parts")
        except Exception as e:
            print(f"    gemini-2.5-flash-image échec: {e}")

    # ── Tentative 2 : gemini-2.0-flash-exp-image-generation ──────────────────
    if client_g:
        try:
            print(f"  🎨 Tentative gemini-2.0-flash-exp-image-generation pour {lang}...")
            response = client_g.models.generate_content(
                model="gemini-2.0-flash-exp-image-generation",
                contents=full_prompt,
                config=gtypes.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                )
            )
            for part in response.parts:
                if part.inline_data is not None:
                    img_bytes = part.inline_data.data
                    print(f"    ✅ Image (gemini-2.0-flash-exp-image-generation) générée ({len(img_bytes)//1024}KB)")
                    return img_bytes
            print("    gemini-2.0-flash-exp-image-generation : pas d'image dans response.parts")
        except Exception as e:
            print(f"    gemini-2.0-flash-exp-image-generation échec: {e}")

    # ── Tentative 3 : Imagen 3 ────────────────────────────────────────────────
    if client_g:
        try:
            print(f"  🎨 Tentative imagen-3.0-generate-001 pour {lang}...")
            response = client_g.models.generate_images(
                model="imagen-3.0-generate-001",
                prompt=full_prompt,
                config=gtypes.GenerateImagesConfig(number_of_images=1, aspect_ratio="1:1")
            )
            if response.generated_images:
                img_bytes = response.generated_images[0].image.image_bytes
                if img_bytes:
                    print(f"    ✅ Image Imagen 3 générée ({len(img_bytes)//1024}KB)")
                    return img_bytes
        except Exception as e:
            print(f"    Imagen 3 échec: {e}")

    # ── Fallback final : Pillow local ─────────────────────────────────────────
    print(f"    ⚠️ Tous les modèles Gemini ont échoué → Fallback Pillow")
    return _generate_pillow_fallback(prompt_text, lang)


def _generate_pillow_fallback(topic_hint, lang):
    """Image minimaliste locale via Pillow — toujours disponible."""
    import math, random, io
    from PIL import Image, ImageDraw

    seed = abs(hash(lang + topic_hint[:40]))
    rng  = random.Random(seed)
    W, H = 1024, 1024
    BG    = (26, 26, 26)
    CORAL = (255, 107, 107)

    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img, "RGBA")

    for x in range(0, W, 64):
        draw.line([(x, 0), (x, H)], fill=(45, 45, 45, 255), width=1)
    for y in range(0, H, 64):
        draw.line([(0, y), (W, y)], fill=(45, 45, 45, 255), width=1)

    cx, cy = int(W * 0.80), int(H * 0.75)
    for r in range(360, 0, -55):
        alpha = max(20, int(15 + (360 - r) * 0.15))
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(255, 107, 107, alpha), width=2)

    tri_cx, tri_cy, tri_r = int(W * 0.32), int(H * 0.42), 180
    pts = [(tri_cx + tri_r * math.cos(math.pi/2 + 2*math.pi*i/3),
            tri_cy - tri_r * math.sin(math.pi/2 + 2*math.pi*i/3)) for i in range(3)]
    draw.polygon(pts, fill=(255, 255, 255, 35), outline=(255, 255, 255, 130))

    for _ in range(18):
        px = rng.randint(50, W-50)
        py = rng.randint(50, H-50)
        pr = rng.randint(2, 5)
        alpha = rng.randint(80, 200)
        color = CORAL if rng.random() > 0.6 else (255, 255, 255)
        draw.ellipse([px-pr, py-pr, px+pr, py+pr], fill=(*color, alpha))

    draw.rectangle([(0, H-12), (W, H)], fill=CORAL)
    draw.rectangle([(36, 36), (80, 80)], fill=CORAL)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()
    print(f"    ✅ Image Pillow générée ({len(img_bytes)//1024}KB)")
    return img_bytes

# ── 5. Telegram : preview & validation ───────────────────────────────────────
def notify_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={'chat_id': TELEGRAM_CHAT_ID, 'text': message})

def send_previews(posts, images):
    print("📤 Envoi des previews sur Telegram...")
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    for lang in ['en', 'fr', 'es']:
        caption = f"🌍 VERSION {lang.upper()}\n\n{posts[lang][:1000]}"
        img_data = images.get(lang)

        if img_data:
            try:
                resp = requests.post(
                    f"{base_url}/sendPhoto",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'caption': caption},
                    files={'photo': ('image.png', img_data, 'image/png')},
                    timeout=30
                )
                if resp.ok:
                    print(f"  ✅ Preview {lang.upper()} envoyé")
                    continue
            except Exception as e:
                print(f"  ⚠️ sendPhoto échec {lang}: {e}")

        requests.post(f"{base_url}/sendMessage",
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': f"⚠️ Sans image\n\n{caption}"})

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Tout",  "callback_data": "publish_all"},
                {"text": "🇬🇧 EN",   "callback_data": "publish_en"},
                {"text": "🇫🇷 FR",   "callback_data": "publish_fr"},
                {"text": "🇪🇸 ES",   "callback_data": "publish_es"},
            ],
            [{"text": "❌ Annuler", "callback_data": "ignore"}]
        ]
    }
    requests.post(f"{base_url}/sendMessage", json={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': "👇 Validation requise pour LinkedIn :",
        'reply_markup': keyboard
    })

def wait_for_decision(timeout=3600):
    print(f"⏳ Attente de validation Telegram (1h max)...")
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    start_time = time.time()
    last_update_id = None

    while (time.time() - start_time) < timeout:
        try:
            params = {'timeout': 30, 'allowed_updates': ['callback_query']}
            if last_update_id:
                params['offset'] = last_update_id + 1
            r = requests.get(f"{base_url}/getUpdates", params=params, timeout=35).json()

            for update in r.get('result', []):
                last_update_id = update['update_id']
                if 'callback_query' in update:
                    cb = update['callback_query']
                    requests.post(f"{base_url}/answerCallbackQuery",
                        json={'callback_query_id': cb['id'], 'text': "✅ Reçu !"})
                    return cb['data']
        except Exception as e:
            print(f"  Polling error: {e}")
            time.sleep(5)

    return 'ignore'

# ── 6. LinkedIn : upload image & publication ──────────────────────────────────
def get_linkedin_urn():
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'X-Restli-Protocol-Version': '2.0.0',
    }
    r = requests.get('https://api.linkedin.com/v2/userinfo', headers=headers)
    print(f"  UserInfo status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        sub  = data.get('sub', '')
        print(f"  ✅ Compte LinkedIn : {data.get('name', '?')} (sub: {sub})")
        return sub if sub.startswith('urn:li:person:') else f"urn:li:person:{sub}"
    print(f"  ❌ UserInfo error: {r.text}")
    return None

def upload_image_linkedin(img_bytes, author_urn):
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'X-Restli-Protocol-Version': '2.0.0',
        'Content-Type': 'application/json',
    }
    payload = {"initializeUploadRequest": {"owner": author_urn}}
    r = requests.post(
        'https://api.linkedin.com/rest/images?action=initializeUpload',
        headers=headers, json=payload
    )
    print(f"  Image init: {r.status_code}")
    if r.status_code != 200:
        print(f"  ❌ Init error: {r.text}")
        return None

    data       = r.json()
    upload_url = data.get('value', {}).get('uploadUrl', '')
    image_urn  = data.get('value', {}).get('image', '')

    if not upload_url or not image_urn:
        print(f"  ❌ Missing uploadUrl/imageUrn: {data}")
        return None

    up = requests.put(upload_url, data=img_bytes, headers={
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'Content-Type': 'application/octet-stream',
    })
    print(f"  Image upload: {up.status_code}")
    return image_urn if up.status_code in [200, 201] else None

def publish_linkedin(text, author_urn, image_urn=None):
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'Content-Type': 'application/json',
        'X-Restli-Protocol-Version': '2.0.0',
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

    r = requests.post('https://api.linkedin.com/rest/posts', headers=headers, json=post_data)
    print(f"  LinkedIn status: {r.status_code}")
    if r.status_code in [200, 201]:
        post_id = (r.headers.get('x-restli-id')
                   or r.headers.get('X-RestLi-Id')
                   or r.headers.get('X-Restli-Id'))
        print(f"  ✅ Post publié (ID: {post_id})")
        return post_id
    print(f"  ❌ Erreur LinkedIn: {r.text}")
    return None

def post_source_comment(post_id, source_url, author_urn):
    if not post_id:
        return
    time.sleep(30)  # Attendre l'indexation
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'Content-Type': 'application/json',
        'X-Restli-Protocol-Version': '2.0.0',
    }
    comment_data = {
        "actor": author_urn,
        "message": {"text": f"📎 Source : {source_url}"}
    }
    r = requests.post(
        f'https://api.linkedin.com/rest/socialActions/{post_id}/comments',
        headers=headers, json=comment_data
    )
    print(f"  Comment status: {r.status_code}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print(f"🚀 AI LINKEDIN POSTER — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 1. News
    print("📡 Recherche des news AI...")
    news = fetch_news()
    if not news:
        print("Aucune news trouvée.")
        return
    print(f"✅ {len(news)} articles trouvés")

    # 2. Scoring
    best_article, score = score_news(news)
    if score < 7:
        notify_telegram(f"⚠️ Score trop bas ({score}/10). Pipeline arrêté.")
        print(f"Score trop bas ({score}). On s'arrête.")
        return

    # 3. Génération posts
    print("✍️ Génération des posts LinkedIn EN/FR/ES...")
    posts = generate_posts(best_article)
    print("✅ Posts générés")

    # 4. Génération images
    print("🎨 Génération des images avec Gemini...")
    image_prompts = posts.get('image_prompts', {})
    images = {}
    for lang in ['en', 'fr', 'es']:
        prompt = image_prompts.get(lang, f"Modern AI technology concept for {lang} audience")
        images[lang] = generate_image(prompt, lang)

    # 5. Preview Telegram
    send_previews(posts, images)
    print("✅ Preview envoyé — en attente de ta validation...")

    # 6. Validation
    decision = wait_for_decision()
    print(f"✅ Décision : {decision}")

    if decision == 'ignore':
        notify_telegram("🚫 Publication annulée.")
        return

    langs_map = {
        'publish_all': ['en', 'fr', 'es'],
        'publish_en':  ['en'],
        'publish_fr':  ['fr'],
        'publish_es':  ['es'],
    }
    langs_to_publish = langs_map.get(decision, [])

    if not langs_to_publish:
        print("Aucune langue à publier.")
        return

    # 7. Récupérer URN LinkedIn
    print("🔑 Identification du compte LinkedIn...")
    author_urn = get_linkedin_urn()
    if not author_urn:
        notify_telegram("❌ Impossible de récupérer le profil LinkedIn. Vérifie le token.")
        return

    # 8. Publication
    for lang in langs_to_publish:
        label = lang.upper()
        print(f"🚀 Publication {label}...")

        img_bytes = images.get(lang)
        image_urn = None
        if img_bytes:
            print(f"  📸 Upload image {label}...")
            image_urn = upload_image_linkedin(img_bytes, author_urn)

        post_id = publish_linkedin(posts.get(lang, ''), author_urn, image_urn)

        if post_id:
            post_source_comment(post_id, best_article['url'], author_urn)
            notify_telegram(f"✅ Post {label} publié sur LinkedIn !")
        else:
            notify_telegram(f"❌ Échec publication {label}.")

    print("=" * 50)
    print("✅ PIPELINE TERMINÉ")
    print("=" * 50)


if __name__ == "__main__":
    main()
