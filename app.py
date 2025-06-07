#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import asyncio
import aiohttp
import time
from datetime import datetime
from typing import List, Dict
import re
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
import random

try:
    from quart import Quart, request, jsonify
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    from telegram.constants import ParseMode
except ImportError as e:
    print(f"Erreur d'import: {e}")
    print("Installez les dépendances avec: pip install -r requirements.txt")
    sys.exit(1)

# Configuration logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Désactiver les logs trop verbeux
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('hypercorn').setLevel(logging.WARNING)

class SimpleSearcher:
    def __init__(self):
        self.session = None
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
        ]

    async def get_session(self):
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=8, connect=3)
            connector = aiohttp.TCPConnector(limit=5, limit_per_host=2)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={'User-Agent': random.choice(self.user_agents)}
            )
        return self.session

    async def search_groups(self, query: str) -> List[Dict]:
        """Recherche simple et rapide"""
        groups = []
        
        try:
            session = await self.get_session()
            
            # Source simple et fiable
            search_url = f"https://tlgrm.eu/channels?search={quote_plus(query)}"
            
            async with session.get(search_url) as response:
                if response.status == 200:
                    html = await response.text()
                    groups = self._parse_simple(html)
                    
        except Exception as e:
            logger.error(f"Erreur recherche: {e}")
            
        # Ajouter des groupes génériques si pas de résultats
        if not groups:
            groups = self._get_default_groups(query)
            
        return groups[:15]  # Limiter à 15

    def _parse_simple(self, html: str) -> List[Dict]:
        """Parser HTML simple"""
        groups = []
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # Chercher tous les liens Telegram
            links = soup.find_all('a', href=re.compile(r't\.me'))
            
            for link in links[:15]:  # Max 15
                try:
                    url = link.get('href', '')
                    if not url.startswith('http'):
                        url = 'https://t.me/' + url.split('/')[-1]
                    
                    title = link.get_text().strip()
                    if not title:
                        title = url.split('/')[-1].replace('@', '')
                    
                    groups.append({
                        'title': title[:50],
                        'link': url,
                        'description': 'Groupe Telegram',
                        'members': 0
                    })
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Erreur parsing: {e}")
            
        return groups

    def _get_default_groups(self, query: str) -> List[Dict]:
        """Groupes par défaut si aucun résultat"""
        defaults = [
            {'title': f'{query.title()} Community', 'link': 'https://t.me/telegram', 'description': 'Communauté Telegram officielle'},
            {'title': f'{query.title()} News', 'link': 'https://t.me/telegram', 'description': 'Actualités et nouvelles'},
            {'title': f'{query.title()} Discussion', 'link': 'https://t.me/telegram', 'description': 'Groupe de discussion'},
        ]
        return defaults

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.searcher = SimpleSearcher()
        self.application = Application.builder().token(token).build()
        self._setup_handlers()

    def _setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("search", self.search_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 Bot de recherche Telegram\n\n"
            "Tapez votre recherche ou utilisez /search <terme>\n\n"
            "Exemples: crypto, musique, tech"
        )

    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Spécifiez votre recherche\nExemple: /search crypto")
            return
        
        query = ' '.join(context.args)
        await self._perform_search(update, query)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.message.text.strip()
        if 2 <= len(query) <= 30:
            await self._perform_search(update, query)

    async def _perform_search(self, update: Update, query: str):
        msg = await update.message.reply_text(f"🔍 Recherche '{query}'...")
        
        try:
            groups = await asyncio.wait_for(
                self.searcher.search_groups(query),
                timeout=10.0
            )
            
            await msg.delete()
            
            if groups:
                await self._send_results(update, query, groups)
            else:
                await update.message.reply_text(f"❌ Aucun résultat pour '{query}'")
                
        except asyncio.TimeoutError:
            await msg.delete()
            await update.message.reply_text("⏱️ Recherche trop longue, réessayez")
        except Exception as e:
            logger.error(f"Erreur: {e}")
            await msg.delete()
            await update.message.reply_text("❌ Erreur de recherche")

    async def _send_results(self, update: Update, query: str, groups: List[Dict]):
        text = f"🎯 **Résultats pour '{query}'**\n📊 {len(groups)} groupes trouvés\n\n"
        
        for i, group in enumerate(groups, 1):
            text += f"🔹 **{i}. {group['title']}**\n🔗 {group['link']}\n\n"
            
            if len(text) > 3500:  # Limite Telegram
                await update.message.reply_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True
                )
                text = ""
        
        if text:
            await update.message.reply_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )

# Application Quart
app = Quart(__name__)
bot_instance = None

@app.route('/')
async def home():
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Telegram Bot</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                margin: 0;
                padding: 50px 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                text-align: center;
                min-height: 100vh;
                box-sizing: border-box;
            }}
            .container {{
                max-width: 400px;
                margin: 0 auto;
                background: rgba(255,255,255,0.1);
                padding: 40px 30px;
                border-radius: 20px;
                backdrop-filter: blur(15px);
                box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            }}
            h1 {{
                margin: 0 0 30px 0;
                font-size: 28px;
                font-weight: 300;
            }}
            .phone {{
                font-size: 28px;
                font-weight: 700;
                color: #fff;
                background: rgba(255,255,255,0.15);
                padding: 20px;
                border-radius: 15px;
                margin: 30px 0;
                letter-spacing: 1px;
            }}
            .status {{
                color: #4CAF50;
                font-size: 16px;
                margin-top: 25px;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
            }}
            .timestamp {{
                font-size: 12px;
                opacity: 0.7;
                margin-top: 15px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Telegram Bot Service</h1>
            <div class="phone">+237651104356</div>
            <div class="status">
                <span>✅</span>
                <span>Service Actif</span>
            </div>
            <div class="timestamp">
                Dernière mise à jour: {datetime.now().strftime("%d/%m/%Y %H:%M")}
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/webhook', methods=['POST'])
async def webhook():
    try:
        data = await request.get_json()
        if data and bot_instance:
            update = Update.de_json(data, bot_instance.application.bot)
            await bot_instance.application.process_update(update)
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/health')
async def health():
    return jsonify({
        'status': 'healthy', 
        'timestamp': datetime.now().isoformat(),
        'bot_active': bot_instance is not None
    })

async def setup_bot():
    global bot_instance
    
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN manquant!")
        return False
    
    try:
        bot_instance = TelegramBot(token)
        await bot_instance.application.initialize()
        await bot_instance.application.start()
        
        webhook_url = os.getenv('WEBHOOK_URL')
        if webhook_url:
            await bot_instance.application.bot.set_webhook(
                url=f"{webhook_url}/webhook",
                allowed_updates=["message"]
            )
            logger.info(f"Webhook configuré: {webhook_url}/webhook")
        
        logger.info("Bot configuré avec succès")
        return True
        
    except Exception as e:
        logger.error(f"Erreur setup bot: {e}")
        return False

async def cleanup():
    global bot_instance
    if bot_instance:
        try:
            await bot_instance.searcher.close()
            await bot_instance.application.stop()
            await bot_instance.application.shutdown()
            logger.info("Nettoyage terminé")
        except Exception as e:
            logger.error(f"Erreur nettoyage: {e}")

async def main():
    logger.info("Démarrage du service...")
    
    success = await setup_bot()
    if not success:
        logger.error("Échec configuration bot")
        sys.exit(1)
    
    port = int(os.getenv('PORT', 8000))
    logger.info(f"Démarrage serveur sur port {port}")
    
    try:
        # Configuration Hypercorn
        from hypercorn.asyncio import serve
        from hypercorn.config import Config
        
        config = Config()
        config.bind = [f"0.0.0.0:{port}"]
        config.workers = 1
        config.worker_connections = 10
        config.keep_alive_timeout = 30
        
        await serve(app, config)
        
    except Exception as e:
        logger.error(f"Erreur serveur: {e}")
        sys.exit(1)
    finally:
        await cleanup()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Arrêt du service")
    except Exception as e:
        logger.error(f"Erreur fatale: {e}")
        sys.exit(1)
