try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Tentative d'extraction par regex si le texte contient du surplus
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
            
    # Ultime tentative : gestion des sauts de ligne dans les chaînes
    try:
        def fix_string_newlines(m):
            return m.group(0).replace('\n', '\\n').replace('\r', '\\r')
        fixed = re.sub(r'"((?:[^"\\]|\\.)*)"', fix_string_newlines, text)
        return json.loads(fixed)
    except:
        raise ValueError(f"Échec critique du parsing JSON. Contenu : {text[:200]}...")

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

Réponds UNIQUEMENT avec ce format JSON :
{{
  "index": 0,
  "score": 9,
  "reason": "Explication courte"
}}"""

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
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
L'image doit être abstraite, technologique, style 'Nano Banana', sans texte.

Réponds UNIQUEMENT en JSON :
{{
  "en": "...",
  "fr": "...",
  "es": "...",
  "image_prompts": {{
    "en": "detailed prompt for image",
    "fr": "detailed prompt for image",
    "es": "detailed prompt for image"
  }}
}}"""

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    return safe_json_parse(response.content[0].text)

# ── 4. Génération d'image (Nano Banana / Gemini 2.0) ──────────────────────────
def generate_image(prompt_text, lang):
    """Génère une image via Nano Banana (Gemini 2.0 Flash) basé sur ton test."""
    print(f"🎨 Appel Gemini 2.0 Flash (Nano Banana) pour {lang}...")
    full_prompt = f"{prompt_text}. Style: high-tech, minimalist, cinematic lighting, 8k resolution, professional LinkedIn cover style. No text."

    try:
        from google import genai
        from google.genai import types as gtypes
        
        client_g = genai.Client(api_key=GEMINI_API_KEY)
        
        response = client_g.models.generate_content(
            model="gemini-2.0-flash", 
            contents=full_prompt,
            config=gtypes.GenerateContentConfig(
                response_modalities=["IMAGE"]
            )
        )
        
        if response.parts:
            for part in response.parts:
                if part.inline_data is not None:
                    print(f"  ✅ Image IA générée avec succès pour {lang}")
                    return part.inline_data.data
                    
        # Vérification alternative des candidats (cas où la réponse est structurée différemment)
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    print(f"  ✅ Image IA générée avec succès pour {lang}")
                    return part.inline_data.data
                
    except Exception as e:
        print(f"  ❌ Échec génération ({lang}): {e}")

    # Fallback Pillow
    print("  ⚠️ Basculement sur Pillow fallback...")
    from PIL import Image, ImageDraw
    import io
    img = Image.new("RGB", (1024, 1024), color=(30, 30, 30))
    d = ImageDraw.Draw(img)
    d.rectangle([50, 50, 974, 974], outline=(255, 100, 100), width=10)
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()

# ── 5. Logique Telegram (Preview & Validation) ────────────────────────────────
def notify_telegram(message):
    url = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={'chat_id': TELEGRAM_CHAT_ID, 'text': message})

def send_previews(posts, images):
    print("📤 Envoi des previews sur Telegram...")
    base_url = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_BOT_TOKEN}"
    
    for lang in ['en', 'fr', 'es']:
        caption = f"🌍 VERSION {lang.upper()}\n\n{posts[lang][:1000]}"
        img_data = images.get(lang)
        
        if img_data:
            requests.post(f"{base_url}/sendPhoto", 
                data={'chat_id': TELEGRAM_CHAT_ID, 'caption': caption},
                files={'photo': ('image.png', img_data, 'image/png')})
        else:
            requests.post(f"{base_url}/sendMessage", 
                json={'chat_id': TELEGRAM_CHAT_ID, 'text': caption})

    # Reproduction de ton clavier d'origine
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Tout", "callback_data": "publish_all"},
                {"text": "🇬🇧 EN", "callback_data": "publish_en"},
                {"text": "🇫🇷 FR", "callback_data": "publish_fr"},
                {"text": "🇪🇸 ES", "callback_data": "publish_es"}
            ],
            [
                {"text": "❌ Annuler", "callback_data": "ignore"}
            ]
        ]
    }
    requests.post(f"{base_url}/sendMessage", 
        json={'chat_id': TELEGRAM_CHAT_ID, 'text': "Validation requise pour LinkedIn :", 'reply_markup': keyboard})

def wait_for_decision(timeout=3600):
    print(f"⏳ Attente de validation Telegram (1h max)...")
    base_url = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_BOT_TOKEN}"
    start_time = time.time()
    last_update_id = None
    
    while (time.time() - start_time) < timeout:
        try:
            params = {'offset': last_update_id, 'timeout': 30}
            r = requests.get(f"{base_url}/getUpdates", params=params).json()
            
            for update in r.get('result', []):
                last_update_id = update['update_id'] + 1
                if 'callback_query' in update:
                    data = update['callback_query']['data']
                    requests.post(f"{base_url}/answerCallbackQuery", 
                        json={'callback_query_id': update['callback_query']['id'], 'text': "Action enregistrée"})
                    return data
        except:
            pass
        time.sleep(5)
    return 'ignore'

# ── 6. Logique LinkedIn (API REST) ────────────────────────────────────────────
def get_linkedin_urn():
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'X-Restli-Protocol-Version': '2.0.0'
    }
    r = requests.get('[https://api.linkedin.com/v2/userinfo](https://api.linkedin.com/v2/userinfo)', headers=headers)
    if r.status_code == 200:
        return f"urn:li:person:{r.json()['sub']}"
    return None

def upload_image_linkedin(img_bytes, author_urn):
    headers = {'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}', 'LinkedIn-Version': LINKEDIN_VERSION}
    payload = {"initializeUploadRequest": {"owner": author_urn}}
    r = requests.post('[https://api.linkedin.com/rest/images?action=initializeUpload](https://api.linkedin.com/rest/images?action=initializeUpload)', 
                      headers=headers, json=payload).json()
    
    image_urn = r['value']['image']
    upload_url = r['value']['uploadUrl']
    requests.put(upload_url, data=img_bytes, headers={'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}'})
    return image_urn

def publish_linkedin(text, author_urn, image_urn=None):
    headers = {
        'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
        'LinkedIn-Version': LINKEDIN_VERSION,
        'Content-Type': 'application/json',
        'X-Restli-Protocol-Version': '2.0.0'
    }
    post_data = {"author": author_urn, "commentary": text, "visibility": "PUBLIC", "lifecycleState": "PUBLISHED"}
    if image_urn:
        post_data["content"] = {"media": {"id": image_urn}}
        
    r = requests.post('[https://api.linkedin.com/rest/posts](https://api.linkedin.com/rest/posts)', headers=headers, json=post_data)
    return r.headers.get('x-restli-id') if r.status_code in [201, 200] else None

def post_source_comment(post_id, source_url, author_urn):
    headers = {'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}', 'LinkedIn-Version': LINKEDIN_VERSION, 'Content-Type': 'application/json'}
    comment_data = {"actor": author_urn, "object": post_id, "message": {"text": f"🔗 Source : {source_url}"}}
    requests.post('[https://api.linkedin.com/rest/socialActions/comments](https://api.linkedin.com/rest/socialActions/comments)', headers=headers, json=comment_data)

# ── Orchestration principale ──────────────────────────────────────────────────
def main():
    print(f"🚀 Démarrage de l'automatisation : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    news = fetch_news()
    if not news:
        print("Aucune news trouvée.")
        return

    best_article, score = score_news(news)
    if score < 7:
        print(f"Score trop bas ({score}). On s'arrête.")
        return
        
    posts = generate_posts(best_article)
    images = {l: generate_image(posts['image_prompts'][l], l) for l in ['en', 'fr', 'es']}
    
    send_previews(posts, images)
    
    decision = wait_for_decision()
    
    if decision == 'ignore':
        notify_telegram("🚫 Publication annulée.")
        print("Publication annulée via Telegram.")
        return
        
    # Logique originale des langues basées sur les callbacks Telegram
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

    print("🔑 Identification du compte LinkedIn...")
    author_urn = get_linkedin_urn()
    if not author_urn:
        notify_telegram("❌ Erreur : impossible de récupérer ton profil LinkedIn. Vérifie le token.")
        return
        
    # Publication
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
            notify_telegram(f"❌ Échec de la publication du post {lang_label}.")

if __name__ == "__main__":
    main()
