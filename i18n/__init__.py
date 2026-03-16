import gettext
import os

_ = lambda s: s

def set_language(lang_code='en'):
    global _
    print(f"🔄 Attempting to set language to: {lang_code}")
    try:
        translation = gettext.translation(
            'messages',
            localedir=os.path.join(os.path.dirname(__file__), 'locales'),
            languages=[lang_code],
            fallback=True
        )
        _ = translation.gettext
        print(f"✅ Language successfully set to: {lang_code}")
    except Exception as e:
        _ = lambda s: s
        print(f"❌ Failed to set language to {lang_code}, falling back to default. Error: {e}")

set_language('en')