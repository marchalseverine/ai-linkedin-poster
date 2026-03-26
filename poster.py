#!/usr/bin/env python3
"""
AI News LinkedIn Auto-Poster — Sévi
Génère, valide via Telegram, publie sur LinkedIn en FR/ES/IT
"""

import os
import json
import time
import base64
import requests
import feedparser
import anthropic
from datetime import datetime, timedelta

# ============================================================
# CONFIGURATION — toutes les valeurs viennent des GitHub Secrets
# ============================================================
CLAUDE_API_KEY        = os.environ["CLAUDE_API_KEY"]
GEMINI_API_KEY        = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID      = os.environ["TELEGRAM_CHAT_ID"]
LINKEDIN_ACCESS_TOKEN = os.environ["LINKEDIN_ACCESS_TOKEN"]

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ============================================================
# ÉTAPE 1 — RECHERCHE DES NEWS AI (dernières 24h via RSS)
# ============================================================
def fetch_ai_news():
    print("📡 Recherche des news AI...")
    feeds = [
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        "https://venturebeat.com/ai/feed/",
        "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "https://thenextweb.com/neural/feed/",
    ]
    articles = []
    cutoff = datetime.now() - timedelta(hours=24)

    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                articles.append({
                    "title":   entry.get("title", ""),
                    "summary": entry.get("summary", "")[:500],
                    "url":     entry.get("link", ""),
                    "source":  feed.feed.get("title", "Unknown"),
                })
        except Exception as e:
            print(f"⚠️  Erreur RSS {feed_url}: {e}")

    print(f"✅ {len(articles)} articles trouvés")
    return articles


# ============================================================
# ÉTAPE 2 — SCORING AVEC CLAUDE
# ============================================================
def score_and_select(articles):
    print("🧠 Scoring des news avec Claude...")
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    articles_text = "\n\n".join([
        f"[{i+1}] {a['title']}\nSource: {a['source']}\nURL: {a['url']}\nRésumé: {a['summary']}"
        for i, a in enumerate(articles)
    ])

    prompt = f"""Tu es expert contenu LinkedIn tech. Voici des news AI des dernières 24h.

{articles_text}

Score chaque news /10 selon :
- Accessibilité (compréhensible sans être expert)
- Impact grand public (ça touche la vie des gens)
- Potentiel d'engagement LinkedIn
- Originalité / fraîcheur

Retourne UNIQUEMENT ce JSON valide, rien d'autre :
{{
  "best_index": <numéro de la meilleure (1-based)>,
  "score": <score /10>,
  "raison": "<pourquoi c'est la meilleure en 1 phrase courte>"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    result = json.loads(response.content[0].text)
    best = articles[result["best_index"] - 1]
    print(f"✅ Meilleure news ({result['score']}/10) : {best['title']}")
    return best, result


# ============================================================
# ÉTAPE 3 — GÉNÉRATION DU CONTENU EN 3 LANGUES
# ============================================================
def generate_content(news):
    print("✍️  Génération des posts LinkedIn en FR/ES/IT...")
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    prompt = f"""Tu es expert contenu LinkedIn tech grand public.

NEWS : {news['title']}
Source : {news['source']}
Résumé : {news['summary']}

Génère un post LinkedIn de vulgarisation en 3 langues (FR, ES, IT).

RÈGLES :
- Hook percutant en 1-2 lignes max (LinkedIn coupe à 3 lignes)
- Explication accessible : pourquoi ça compte pour tout le monde
- Ton direct, énergique, sans jargon inutile
- 5-7 lignes max visibles
- 2-3 hashtags à la fin
- Max 2 emojis au total
- Terminer par une question ouverte
- PAS de lien dans le post (sera en commentaire)
- Signature : Sévi
- ES et IT = adaptation culturelle, PAS traduction littérale

Retourne UNIQUEMENT ce JSON valide :
{{
  "fr": "<post complet français>",
  "es": "<post complet espagnol>",
  "it": "<post complet italien>",
  "image_prompt_fr": "<prompt anglais pour image LinkedIn FR — contexte français>",
  "image_prompt_es": "<prompt anglais pour image LinkedIn ES — contexte espagnol>",
  "image_prompt_it": "<prompt anglais pour image LinkedIn IT — contexte italien>"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    content = json.loads(response.content[0].text)
    print("✅ Posts générés en FR, ES, IT")
    return content


# ============================================================
# ÉTAPE 4 — GÉNÉRATION DES IMAGES AVEC GEMINI
# ============================================================
def generate_images(content):
    print("🎨 Génération des images avec Gemini...")
    images = {}

    for lang in ["fr", "es", "it"]:
        prompt = (
            f"{content[f'image_prompt_{lang}']}. "
            "Style: minimalist tech, solid dark background #1A1A1A, "
            "bold white typography, one coral accent #FF6B6B, "
            "no photos, no gradients, square 1:1 format, professional LinkedIn visual."
        )

        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp-image-generation:generateContent?key={GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}
            }
        )

        data = response.json()
        try:
            for part in data["candidates"][0]["content"]["parts"]:
                if "inlineData" in part:
                    images[lang] = part["inlineData"]["data"]  # base64
                    print(f"  ✅ Image {lang.upper()} générée")
                    break
        except Exception as e:
            print(f"  ⚠️  Image {lang.upper()} échouée: {e} — on continue sans image")
            images[lang] = None

        time.sleep(3)  # Éviter le rate limiting Gemini

    return images


# ============================================================
# ÉTAPE 5 — ENVOI SUR TELEGRAM POUR VALIDATION
# ============================================================
def send_telegram_preview(news, scoring, content):
    print("📱 Envoi du preview sur Telegram...")

    # Envoyer les images d'abord (une par langue)
    flags = {"fr": "🇫🇷", "es": "🇪🇸", "it": "🇮🇹"}

    message = (
        f"🤖 *AI News du {datetime.now().strftime('%d/%m/%Y')}*\n\n"
        f"📰 *News sélectionnée — {scoring['score']}/10*\n"
        f"_{news['title']}_\n"
        f"_{scoring['raison']}_\n\n"
        f"---\n\n"
        f"🇫🇷 *FRANÇAIS*\n{content['fr']}\n\n"
        f"---\n\n"
        f"🇪🇸 *ESPAÑOL*\n{content['es']}\n\n"
        f"---\n\n"
        f"🇮🇹 *ITALIANO*\n{content['it']}\n\n"
        f"---\n"
        f"📎 Source : {news['url']}"
    )

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Publier FR", "callback_data": "publish_fr"},
                {"text": "✅ Publier ES", "callback_data": "publish_es"},
                {"text": "✅ Publier IT", "callback_data": "publish_it"},
            ],
            [
                {"text": "🌍 Publier TOUT", "callback_data": "publish_all"},
                {"text": "❌ Ignorer", "callback_data": "skip"},
            ]
        ]
    }

    requests.post(f"{TELEGRAM_API}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "reply_markup": keyboard,
    })

    print("✅ Preview envoyé sur Telegram — en attente de ta validation...")


# ============================================================
# ATTENTE DE LA VALIDATION TELEGRAM (polling simple)
# ============================================================
def wait_for_approval(timeout_minutes=60):
    # Récupérer l'offset actuel pour ignorer les anciens messages
    r = requests.get(f"{TELEGRAM_API}/getUpdates").json()
    offset = (r["result"][-1]["update_id"] + 1) if r["result"] else 0

    start = time.time()
    while time.time() - start < timeout_minutes * 60:
        r = requests.get(f"{TELEGRAM_API}/getUpdates", params={
            "offset": offset,
            "timeout": 30
        }).json()

        for update in r.get("result", []):
            offset = update["update_id"] + 1

            if "callback_query" in update:
                callback = update["callback_query"]
                decision = callback["data"]

                # Confirmer au bot que le bouton a été reçu
                requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={
                    "callback_query_id": callback["id"],
                    "text": "✅ Reçu !"
                })

                print(f"✅ Décision reçue : {decision}")
                return decision

    print("⏰ Timeout — aucune validation reçue en 60 min")
    return "timeout"


# ============================================================
# ÉTAPE 6 — PUBLICATION SUR LINKEDIN
# ============================================================
def get_linkedin_user_id():
    r = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}"}
    )
    return r.json()["sub"]


def upload_image_to_linkedin(image_b64, user_id):
    if not image_b64:
        return None

    headers = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "LinkedIn-Version": "202408",
    }

    # Étape 1 — Initialiser l'upload
    r = requests.post(
        "https://api.linkedin.com/rest/images?action=initializeUpload",
        headers=headers,
        json={"initializeUploadRequest": {"owner": f"urn:li:person:{user_id}"}}
    )
    upload_url = r.json()["value"]["uploadUrl"]
    image_urn  = r.json()["value"]["image"]

    # Étape 2 — Upload l'image binaire
    requests.put(
        upload_url,
        data=base64.b64decode(image_b64),
        headers={"Content-Type": "image/png"}
    )

    return image_urn


def publish_post(text, image_urn, user_id):
    headers = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "LinkedIn-Version": "202408",
    }

    body = {
        "author": f"urn:li:person:{user_id}",
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": []
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False
    }

    if image_urn:
        body["content"] = {"media": {"id": image_urn}}

    r = requests.post(
        "https://api.linkedin.com/rest/posts",
        headers=headers,
        json=body
    )

    post_id = r.headers.get("x-restli-id") or r.json().get("id")
    print(f"  ✅ Post publié : {post_id}")
    return post_id


def add_source_comment(post_id, source_url, user_id):
    time.sleep(30)  # Attendre 30s (bonne pratique LinkedIn)

    post_urn = f"urn:li:share:{post_id}" if "share" not in post_id else post_id

    requests.post(
        f"https://api.linkedin.com/rest/socialActions/{post_urn}/comments",
        headers={
            "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
            "Content-Type": "application/json",
            "LinkedIn-Version": "202408",
        },
        json={
            "actor": f"urn:li:person:{user_id}",
            "message": {"text": f"📎 Source : {source_url}"}
        }
    )
    print(f"  ✅ Commentaire source ajouté")


def publish_language(lang, content, images, news, user_id):
    print(f"\n🚀 Publication {lang.upper()}...")
    image_urn = upload_image_to_linkedin(images.get(lang), user_id)
    post_id   = publish_post(content[lang], image_urn, user_id)
    if post_id:
        add_source_comment(post_id, news["url"], user_id)


# ============================================================
# MAIN — ORCHESTRATION COMPLÈTE
# ============================================================
def main():
    print("\n" + "="*50)
    print("🤖 AI LINKEDIN POSTER — Démarrage")
    print("="*50 + "\n")

    # 1. News
    articles = fetch_ai_news()
    if not articles:
        print("❌ Aucune news trouvée — arrêt")
        return

    # 2. Scoring
    news, scoring = score_and_select(articles)

    # 3. Contenu
    content = generate_content(news)

    # 4. Images
    images = generate_images(content)

    # 5. Telegram
    send_telegram_preview(news, scoring, content)
    decision = wait_for_approval(timeout_minutes=60)

    # 6. Publication
    if decision == "skip" or decision == "timeout":
        print("\n⏭️  Publication annulée ou timeout")
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": "⏭️ Post ignoré pour aujourd'hui."
        })
        return

    user_id = get_linkedin_user_id()
    langs_to_publish = ["fr", "es", "it"] if decision == "publish_all" else [decision.replace("publish_", "")]

    for lang in langs_to_publish:
        publish_language(lang, content, images, news, user_id)
        time.sleep(10)  # Délai entre les posts

    print("\n" + "="*50)
    print("✅ PIPELINE TERMINÉ")
    print("="*50)

    requests.post(f"{TELEGRAM_API}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"✅ {len(langs_to_publish)} post(s) publiés sur LinkedIn !"
    })


if __name__ == "__main__":
    main()
