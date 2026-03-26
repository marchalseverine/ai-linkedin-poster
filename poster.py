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
LINKEDIN_VERSION = "202501"

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

    prompt = f"""Tu es Sévi Marchal, Product Operations Manager, experte en IA, basée à Valence (Espagne).
Tu crées des posts LinkedIn de vulgarisation IA accessibles à tous mais qui montrent une vraie expertise.

Article source :
Titre : {article['title']}
Résumé : {article['summary']}
URL : {article['url']}

Génère 3 posts LinkedIn adaptés culturellement (PAS une traduction littérale) :
- EN : Anglais, audience internationale, ton professionnel mais accessible
- FR : Français, ton direct et personnel, réseau francophone de Sévi
- ES : Espagnol, "tú" naturel, réseau Valencia/Madrid

Format de chaque post :
- Hook percutant sur 1-2 lignes
- 5-7 lignes de corps (vulgarisation + expertise)
- 1 question finale pour engager
- Signature : "Sévi"
- 2-3 hashtags max (ex: #AI #Tech)
- PAS de liens dans le post (source ajoutée en commentaire automatiquement)

Génère aussi 1 prompt de génération d'image par langue, adapté culturellement.
Style image : fond sombre #1A1A1A, typographie blanche bold, accent corail #FF6B6B, minimaliste, professionnel.

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

    return json.loads(text)

# ── 4. Generate image with Gemini ─────────────────────────────────────────────
def generate_image_gemini(prompt_text, lang):
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)

        full_prompt = (
            f"Create a modern minimalist tech illustration for LinkedIn. "
            f"Dark background (#1A1A1A), white bold typography, coral accent (#FF6B6B). "
            f"Clean, professional, social media optimized. No text in the image. "
            f"Concept: {prompt_text}"
        )

        # Tentative 1 : Imagen 3
        try:
            model = genai.ImageGenerationModel("imagen-3.0-generate-001")
            result = model.generate_images(
                prompt=full_prompt,
                number_of_images=1,
                aspect_ratio="1:1",
            )
            if result.images:
                return result.images[0]._image_bytes
        except Exception as e1:
            print(f"    Imagen 3 échec: {e1}")

        # Tentative 2 : Gemini 2.0 Flash avec output image
        try:
            import google.generativeai.types as gtypes
            model = genai.GenerativeModel("gemini-2.0-flash-exp")
            response = model.generate_content(
                full_prompt,
                generation_config={"response_mime_type": "image/png"}
            )
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    import base64
                    return base64.b64decode(part.inline_data.data)
        except Exception as e2:
            print(f"    Gemini Flash image échec: {e2}")

        return None

    except Exception as e:
        print(f"    Erreur génération image: {e}")
        return None

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
        img = generate_image_gemini(prompt, lang)
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
