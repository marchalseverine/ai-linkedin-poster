"""
Test rapide : génération d'image avec Gemini (même moteur que Nano Banana)
Usage : GEMINI_API_KEY=ta_clé python3 test_gemini_image.py
"""

import os
import sys

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ Erreur : variable GEMINI_API_KEY manquante")
    print("   Lance avec : GEMINI_API_KEY=ta_clé python3 test_gemini_image.py")
    sys.exit(1)

print("🔑 Clé Gemini trouvée")

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("❌ SDK non installé — lance : pip install google-genai")
    sys.exit(1)

print("📦 SDK google-genai OK")

client = genai.Client(api_key=GEMINI_API_KEY)

prompt = (
    "A modern minimalist tech illustration for LinkedIn. "
    "Dark background, coral red accent shapes, white geometric elements. "
    "No text in the image. Clean, professional, abstract."
)

flash_models = [
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image-preview",
]

image_found = False
for model_name in flash_models:
    if image_found:
        break
    print(f"🎨 Génération avec {model_name}...")
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"]
            )
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                img_bytes = part.inline_data.data
                with open("gemini_test_output.png", "wb") as f:
                    f.write(img_bytes)
                print(f"✅ Image générée ({model_name}) : {len(img_bytes)//1024}KB → gemini_test_output.png")
                image_found = True
                break
        if not image_found:
            print(f"⚠️  {model_name} : réponse reçue mais pas d'image dans les parts")
            print("   Parts reçus :", [type(p).__name__ for p in response.candidates[0].content.parts])
    except Exception as e:
        print(f"❌ {model_name} échec: {e}")

if not image_found:
    print("\n🎨 Tentative avec imagen-4.0-generate-001...")
    try:
        response2 = client.models.generate_images(
            model="imagen-4.0-generate-001",
            prompt=prompt,
            config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="1:1")
        )
        if response2.generated_images:
            img_bytes = response2.generated_images[0].image.image_bytes
            with open("gemini_test_output.png", "wb") as f:
                f.write(img_bytes)
            print(f"✅ Imagen 3 OK : {len(img_bytes)//1024}KB → gemini_test_output.png")
        else:
            print("❌ Imagen 3 : aucune image générée")
    except Exception as e2:
        print(f"❌ Imagen 3 échec aussi: {e2}")
        print("\n💡 Cause probable : le billing Google Cloud n'est pas activé.")
        print("   Activation : https://aistudio.google.com → ton projet → Enable billing")
