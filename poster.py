import os
import json
import time
import re
import feedparser
import requests
import anthropic
from datetime import datetime, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
CLAUDE_API_KEY        = os.environ['CLAUDE_API_KEY']
GEMINI_API_KEY        = os.environ['GEMINI_API_KEY']
TELEGRAM_BOT_TOKEN    = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID      = os.environ['TELEGRAM_CHAT_ID']
LINKEDIN_ACCESS_TOKEN = os.environ['LINKEDIN_ACCESS_TOKEN']

# LinkedIn REST API version — format YYYYMM (6 chiffres, PAS de jour)
# LinkedIn supporte les versions sur ~2 ans — 202503 = Mars 2025, dans la fenêtre active
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

# ── 2. Score & select best news ───────────────────────────────────────────────
def score_news(articles):
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    articles_text = "\n".join(
        [f"{i+1}. {a['title']}\n{a['summary'][:200]}" for i, a in enumerate(articles)]
    )

    prompt = f"""Tu es expert en contenu LinkedIn tech. Voici {len(articles)} articles AI des dernières 24h.

{articles_text}

Sélectionne le MEILLEUR article pour un post LinkedIn de vulgarisation AI destiné à un public mixte (tech + recruteurs).
Critères : accessibilité, impact, potentiel d'engagement, originalité.

Réponds UNIQUEMENT avec ce JSON valide (sans markdown, sans backticks) :
{{"index": 0, "score": 8.5, "title": "titre de l'article", "reason": "raison en 1 phrase"}}

L'index doit être entre 0 et {len(articles)-1}."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    # Nettoie les balises markdown si Claude en ajoute
    text = re.sub(r'```(?:json)?\s*', '', text).strip('`').strip()

    result = json.loads(text)
    return articles[result['index']], result['score']

# ── 3. Generate posts EN / FR / ES ────────────────────────────────────────────
def generate_posts(article):
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    prompt = f"""Tu es une experte en IA qui écrit des posts LinkedIn de vulgarisation pour un public mixte (tech + non-tech).
Les posts doivent sonner naturels et humains, PAS comme un texte généré par une IA.

Article source :
Titre : {article['title']}
Résumé : {article['summary']}
URL : {article['url']}

Génère 3 posts LinkedIn adaptés culturellement (PAS une traduction littérale) :
- EN : Anglais, audience internationale, ton conversationnel et direct
- FR : Français, ton direct et personnel, style proche d'un post humain
- ES : Espagnol, "tú" naturel, réseau Valencia/Madrid

Règles strictes d'écriture :
- PAS de tiret long (—) ni de tiret moyen (–), utilise des virgules ou des points à la place
- PAS de formulations trop polies ou corporate
- PAS de signature, PAS de nom en fin de post
- PAS de liens dans le post (source ajoutée en commentaire automatiquement)
- Phrases courtes et directes, style naturel comme un vrai post humain
- Hook percutant sur 1-2 lignes (pas de "Voici" ou "Découvrez")
- 5-7 lignes de corps (vulgarisation + point de vue personnel)
- 1 question finale pour engager les commentaires
- 2-3 hashtags max en fin de post (ex: #AI #Tech)

Génère aussi 1 prompt de génération d'image par langue (description visuelle uniquement, pas de texte dans l'image).
Style image : fond sombre, tons corail et blanc, minimaliste, tech, professionnel LinkedIn.

Réponds UNIQUEMENT avec ce JSON valide (sans markdown, sans backticks) :
{{
  "en": "texte complet du post en anglais",
  "fr": "texte complet du post en français",
  "es": "texte complet du post en espagnol",
  "image_prompts": {{
    "en": "image generation prompt for English/international audience",
    "fr": "image generation prompt for French audience",
    "es": "image generation prompt for Spanish audience"
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

    # Nettoyage : supprime les caractères trop "AI" dans chaque post
    for lang in ['en', 'fr', 'es']:
        if lang in result:
            result[lang] = clean_post_text(result[lang])

    return result

# ── 3b. Clean AI-typical characters ──────────────────────────────────────────
def clean_post_text(text):
    """Supprime les caractères typiquement IA et humanise le texte."""
    # Em-dash et en-dash → virgule ou point selon contexte
    text = text.replace(' — ', ', ')
    text = text.replace('—', ', ')
    text = text.replace(' – ', ', ')
    text = text.replace('–', '-')
    # Guillemets typographiques → guillemets droits (plus naturels en post)
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    # Ellipses fancy → simple
    text = text.replace('\u2026', '...')
    return text.strip()

# ── 4. Generate image ─────────────────────────────────────────────────────────
def generate_image(prompt_text, lang):
    full_prompt = (
        "A modern minimalist tech illustration for LinkedIn. "
        "Dark background (#1A1A1A), coral red accent color (#FF6B6B), "
        "white geometric shapes, professional and clean. "
        "No text, no words, no letters in the image. Abstract concept only. "
        f"Topic: {prompt_text}"
    )

    # Tentative 1 : Gemini gemini-2.0-flash-exp (moteur de Nano Banana)
    try:
        from google import genai
        from google.genai import types as gtypes

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=full_prompt,
            config=gtypes.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"]
            )
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                img_bytes = part.inline_data.data
                print(f"    ✅ Image Gemini (Nano Banana) générée ({len(img_bytes)//1024}KB)")
                return img_bytes
        print("    Gemini : réponse reçue mais pas d'image")
    except Exception as e:
        print(f"    Gemini echec: {e}")

    # Tentative 2 : Imagen 3 (nécessite billing Google Cloud)
    try:
        from google import genai
        from google.genai import types as gtypes

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_images(
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
        print(f"    Imagen 3 echec: {e}")

    # Tentative 3 : Génération locale avec Pillow — toujours disponible
    print(f"    → Fallback Pillow (local)...")
    return generate_branded_image_local(prompt_text, lang)


def generate_branded_image_local(topic_hint, lang):
    """
    Génère une image branded minimaliste tech avec Pillow.
    Fond sombre #1A1A1A, accents corail #FF6B6B, formes géométriques.
    Aucune API externe — toujours disponible.
    """
    import math, random, io
    from PIL import Image, ImageDraw

    seed = abs(hash(lang + topic_hint[:40]))
    rng  = random.Random(seed)
    W, H = 1024, 1024
    BG    = (26, 26, 26)
    CORAL = (255, 107, 107)

    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img, "RGBA")

    # Grille subtile en fond
    for x in range(0, W, 64):
        draw.line([(x, 0), (x, H)], fill=(45, 45, 45, 255), width=1)
    for y in range(0, H, 64):
        draw.line([(0, y), (W, y)], fill=(45, 45, 45, 255), width=1)

    # Cercles concentriques corail (bas-droite)
    cx, cy = int(W * 0.80), int(H * 0.75)
    for r in range(360, 0, -55):
        alpha = max(20, int(15 + (360 - r) * 0.15))
        draw.ellipse([cx-r, cy-r, cx+r, cy+r],
                     outline=(255, 107, 107, alpha), width=2)

    # Grand triangle blanc (centre-gauche)
    tri_cx, tri_cy, tri_r = int(W * 0.32), int(H * 0.42), 180
    pts = [(tri_cx + tri_r * math.cos(math.pi/2 + 2*math.pi*i/3),
            tri_cy - tri_r * math.sin(math.pi/2 + 2*math.pi*i/3)) for i in range(3)]
    draw.polygon(pts, fill=(255, 255, 255, 35), outline=(255, 255, 255, 130))

    # Hexagone corail (haut-droite)
    hx, hy, hr = int(W * 0.70), int(H * 0.28), 110
    hpts = [(hx + hr * math.cos(math.pi/6 + math.pi*i/3),
             hy + hr * math.sin(math.pi/6 + math.pi*i/3)) for i in range(6)]
    draw.polygon(hpts, fill=(255, 107, 107, 30), outline=(255, 107, 107, 160))

    # Petit carré blanc rotatif (centre)
    sq_cx, sq_cy, sq_r = int(W * 0.55), int(H * 0.55), 55
    angle = math.pi / 6
    sqpts = [(sq_cx + sq_r * math.cos(angle + math.pi/2 * i),
              sq_cy + sq_r * math.sin(angle + math.pi/2 * i)) for i in range(4)]
    draw.polygon(sqpts, fill=(255, 255, 255, 25), outline=(255, 255, 255, 110))

    # Lignes diagonales corail
    for x1, y1, x2, y2 in [(80, 300, 260, 160), (200, 680, 380, 560), (620, 100, 750, 220)]:
        draw.line([(x1, y1), (x2, y2)], fill=(255, 107, 107, 140), width=2)

    # Points lumineux (constellation)
    for _ in range(18):
        px = rng.randint(50, W-50)
        py = rng.randint(50, H-50)
        pr = rng.randint(2, 5)
        alpha = rng.randint(80, 200)
        color = CORAL if rng.random() > 0.6 else (255, 255, 255)
        draw.ellipse([px-pr, py-pr, px+pr, py+pr], fill=(*color, alpha))

    # Barre corail en bas + accent top-left
    draw.rectangle([(0, H-12), (W, H)], fill=CORAL)
    draw.rectangle([(36, 36), (80, 80)], fill=CORAL)
    draw.rectangle([(88, 36), (102, 80)], fill=(255, 107, 107, 180))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()
    print(f"    ✅ Image générée en local ({len(img_bytes)//1024}KB)")
    return img_bytes

# ── 5. Send Telegram preview (images + textes + boutons) ──────────────────────
def send_telegram_preview(posts, article, images, score):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    date_str = datetime.now().strftime("%d/%m/%Y")

    langs = [
        ('en', '🇬🇧 EN'),
        ('fr', '🇫🇷 FR'),
        ('es', '🇪🇸 ES'),
    ]

    # Envoie chaque post avec son image en caption
    for lang_code, lang_label in langs:
        post_text = posts.get(lang_code, '')
        img_bytes = images.get(lang_code)
        caption = f"{lang_label}\n\n{post_text[:1024]}"  # limite Telegram caption

        if img_bytes:
            try:
                resp = requests.post(
                    f"{base_url}/sendPhoto",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'caption': caption},
                    files={'photo': ('image.png', img_bytes, 'image/png')},
                    timeout=30
                )
                if not resp.ok:
                    raise Exception(resp.text)
                print(f"  ✅ Image {lang_label} envoyée sur Telegram")
                continue
            except Exception as e:
                print(f"  ⚠️  sendPhoto échec pour {lang_code}: {e}")

        # Fallback texte seul
        requests.post(f"{base_url}/sendMessage", json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': f"⚠️ Sans image\n\n{caption}"
        })

    # Message de validation avec boutons inline
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ EN", "callback_data": "publish_en"},
                {"text": "✅ FR", "callback_data": "publish_fr"},
                {"text": "✅ ES", "callback_data": "publish_es"},
            ],
            [
                {"text": "🌍 TOUT publier", "callback_data": "publish_all"},
                {"text": "❌ Ignorer", "callback_data": "ignore"},
            ]
        ]
    }

    requests.post(f"{base_url}/sendMessage", json={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': (
            f"🤖 AI News du {date_str}\n"
            f"📰 {article['title']}\n"
            f"⭐ Score : {score}/10\n"
            f"📎 Source : {article['url']}\n\n"
            f"👇 Que veux-tu publier ?"
        ),
        'reply_markup': keyboard
    })

# ── 6. Wait for Telegram validation ──────────────────────────────────────────
def wait_for_validation(timeout=3600):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    last_update_id = None
    start_time = time.time()

    while time.time() - start_time < timeout:
        params = {'timeout': 30, 'allowed_updates': ['callback_query']}
        if last_update_id:
            params['offset'] = last_update_id + 1

        try:
            resp = requests.get(f"{base_url}/getUpdates", params=params, timeout=35)
            updates = resp.json().get('result', [])

            for update in updates:
                last_update_id = update['update_id']
                if 'callback_query' in update:
                    callback = update['callback_query']
                    requests.post(f"{base_url}/answerCallbackQuery", json={
                        'callback_query_id': callback['id'],
                        'text': '✅ Reçu !'
                    })
                    return callback['data']

        except Exception as e:
            print(f"  Polling error: {e}")
            time.sleep(5)

    return 'ignore'

# ── 7. Get LinkedIn member URN ────────────────────────────────────────────────
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
        name = data.get('name', 'inconnu')
        print(f"  ✅ Compte LinkedIn identifié : {name} (sub: {sub})")
        # sub peut être "urn:li:person:XXX" ou juste "XXX"
        if sub.startswith('urn:li:person:'):
            return sub
        else:
            return f"urn:li:person:{sub}"
    else:
        print(f"  ❌ UserInfo error: {resp.text}")
        return None

# ── 8. Upload image to LinkedIn ───────────────────────────────────────────────
def upload_image_linkedin(img_bytes, author_urn):
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'X-Restli-Protocol-Version': '2.0.0',
        'Content-Type': 'application/json',
    }

    # Étape 1 : initialiser l'upload
    init_payload = {"initializeUploadRequest": {"owner": author_urn}}
    resp = requests.post(
        'https://api.linkedin.com/rest/images?action=initializeUpload',
        headers=headers,
        json=init_payload
    )
    print(f"  Image init: {resp.status_code}")
    if resp.status_code != 200:
        print(f"  ❌ Init error: {resp.text}")
        return None

    data = resp.json()
    upload_url = data.get('value', {}).get('uploadUrl', '')
    image_urn  = data.get('value', {}).get('image', '')

    if not upload_url or not image_urn:
        print(f"  ❌ Missing uploadUrl or image URN: {data}")
        return None

    # Étape 2 : upload binaire
    upload_resp = requests.put(
        upload_url,
        headers={
            'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
            'Content-Type': 'application/octet-stream',
        },
        data=img_bytes
    )
    print(f"  Image upload: {upload_resp.status_code}")
    if upload_resp.status_code not in [200, 201]:
        print(f"  ❌ Upload error: {upload_resp.text}")
        return None

    return image_urn

# ── 9. Publish post on LinkedIn ───────────────────────────────────────────────
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
        post_data["content"] = {
            "media": {"id": image_urn}
        }

    resp = requests.post(
        'https://api.linkedin.com/rest/posts',
        headers=headers,
        json=post_data
    )

    print(f"  LinkedIn status: {resp.status_code}")

    if resp.status_code == 201:
        post_id = (
            resp.headers.get('x-restli-id')
            or resp.headers.get('X-RestLi-Id')
            or resp.headers.get('X-Restli-Id')
        )
        print(f"  ✅ Post publié (ID: {post_id})")
        return post_id
    else:
        print(f"  ❌ Erreur LinkedIn: {resp.text}")
        return None

# ── 10. Post source comment ───────────────────────────────────────────────────
def post_source_comment(post_id, source_url, author_urn):
    if not post_id:
        return

    time.sleep(30)  # Attendre que le post soit indexé

    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'X-Restli-Protocol-Version': '2.0.0',
        'Content-Type': 'application/json',
    }

    comment_data = {
        "actor": author_urn,
        "message": {"text": f"📎 Source : {source_url}"}
    }

    resp = requests.post(
        f'https://api.linkedin.com/rest/socialActions/{post_id}/comments',
        headers=headers,
        json=comment_data
    )
    print(f"  Comment status: {resp.status_code}")

# ── Helpers ───────────────────────────────────────────────────────────────────
def notify_telegram(message):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={'chat_id': TELEGRAM_CHAT_ID, 'text': message}
    )

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("🤖 AI LINKEDIN POSTER — Démarrage")
    print("=" * 50)

    # Étape 1 : News
    print("📡 Recherche des news AI...")
    articles = fetch_news()
    print(f"✅ {len(articles)} articles trouvés")

    # Étape 2 : Scoring
    print("🧠 Scoring des news avec Claude...")
    best_article, score = score_news(articles)
    print(f"✅ Meilleure news ({score}/10) : {best_article['title']}")

    # Étape 3 : Génération posts EN/FR/ES
    print("✍️  Génération des posts LinkedIn en EN/FR/ES...")
    posts = generate_posts(best_article)
    print("✅ Posts générés en EN, FR, ES")

    # Étape 4 : Génération images
    print("🎨 Génération des images avec Gemini...")
    images = {}
    image_prompts = posts.get('image_prompts', {})

    for lang in ['en', 'fr', 'es']:
        prompt = image_prompts.get(lang, f"Modern AI technology concept for {lang} audience")
        img = generate_image(prompt, lang)
        if img:
            images[lang] = img
            print(f"  ✅ Image {lang.upper()} générée ({len(img)} bytes)")
        else:
            images[lang] = None
            print(f"  ⚠️  Image {lang.upper()} ignorée — post publié sans image")

    # Étape 5 : Preview Telegram
    print("📱 Envoi du preview sur Telegram...")
    send_telegram_preview(posts, best_article, images, score)
    print("✅ Preview envoyé sur Telegram — en attente de ta validation...")

    # Étape 6 : Validation
    decision = wait_for_validation()
    print(f"✅ Décision reçue : {decision}")

    if decision == 'ignore':
        notify_telegram("❌ Post ignoré pour aujourd'hui.")
        print("❌ Post ignoré.")
        return

    # Langues à publier
    if decision == 'publish_all':
        langs_to_publish = ['en', 'fr', 'es']
    elif decision == 'publish_en':
        langs_to_publish = ['en']
    elif decision == 'publish_fr':
        langs_to_publish = ['fr']
    elif decision == 'publish_es':
        langs_to_publish = ['es']
    else:
        langs_to_publish = []

    if not langs_to_publish:
        print("Aucune langue à publier.")
        return

    # Étape 7 : Récupérer URN LinkedIn (= quel compte)
    print("🔑 Identification du compte LinkedIn...")
    author_urn = get_linkedin_urn()
    if not author_urn:
        notify_telegram("❌ Erreur : impossible de récupérer ton profil LinkedIn. Vérifie le token.")
        return

    # Étape 8 : Publication
    for lang in langs_to_publish:
        lang_label = lang.upper()
        print(f"🚀 Publication {lang_label}...")

        post_text = posts.get(lang, '')
        img_bytes = images.get(lang)

        # Upload image si disponible
        image_urn = None
        if img_bytes:
            print(f"  📸 Upload image {lang_label}...")
            image_urn = upload_image_linkedin(img_bytes, author_urn)

        # Publier le post
        post_id = publish_linkedin(post_text, author_urn, image_urn)

        if post_id:
            post_source_comment(post_id, best_article['url'], author_urn)
            notify_telegram(f"✅ Post {lang_label} publié sur LinkedIn !")
        else:
            notify_telegram(f"❌ Erreur lors de la publication {lang_label}.")

    print("=" * 50)
    print("✅ PIPELINE TERMINÉ")
    print("=" * 50)


if __name__ == "__main__":
    main()
