import os
import asyncio
import aiohttp
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from urllib.parse import quote, urljoin
import re
import json
import threading

# Configuration du logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class TelegramGroupSearcher:
    def __init__(self):
        self.ua = UserAgent()
        self.session = None
        self.search_engines = [
            'https://tlgrm.eu/search?q=',
            'https://tgstat.com/search?q=',
            'https://telegram-group.com/search?q=',
            'https://t.me/s/',  # Pour les canaux publics
        ]
        
    async def create_session(self):
        """Crée une session HTTP avec headers personnalisés"""
        if not self.session:
            connector = aiohttp.TCPConnector(limit=10)
            timeout = aiohttp.ClientTimeout(total=15)
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'User-Agent': self.ua.random,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
                    'Accept-Encoding': 'gzip, deflate',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache'
                }
            )
    
    async def search_tlgrm_eu(self, keyword):
        """Recherche sur tlgrm.eu"""
        results = []
        try:
            url = f"https://tlgrm.eu/search?q={quote(keyword)}"
            async with self.session.get(url) as response:
                if response.status == 200:
                    text = await response.text()
                    soup = BeautifulSoup(text, 'html.parser')
                    
                    # Extraire les liens des groupes
                    for item in soup.find_all('div', class_='result-item')[:10]:
                        title_elem = item.find('h3') or item.find('a')
                        link_elem = item.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            link = link_elem['href']
                            if not link.startswith('http'):
                                link = urljoin('https://tlgrm.eu', link)
                            results.append({'title': title, 'link': link})
        except Exception as e:
            logger.error(f"Erreur tlgrm.eu: {e}")
        return results
    
    async def search_tgstat(self, keyword):
        """Recherche sur tgstat.com"""
        results = []
        try:
            url = f"https://tgstat.com/search?q={quote(keyword)}"
            async with self.session.get(url) as response:
                if response.status == 200:
                    text = await response.text()
                    soup = BeautifulSoup(text, 'html.parser')
                    
                    for item in soup.find_all('div', class_='channel-card')[:10]:
                        title_elem = item.find('div', class_='channel-title')
                        link_elem = item.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            link = link_elem['href']
                            if not link.startswith('http'):
                                link = urljoin('https://tgstat.com', link)
                            results.append({'title': title, 'link': link})
        except Exception as e:
            logger.error(f"Erreur tgstat: {e}")
        return results
    
    async def search_telegram_group_com(self, keyword):
        """Recherche sur telegram-group.com"""
        results = []
        try:
            url = f"https://telegram-group.com/search?q={quote(keyword)}"
            async with self.session.get(url) as response:
                if response.status == 200:
                    text = await response.text()
                    soup = BeautifulSoup(text, 'html.parser')
                    
                    for item in soup.find_all('div', class_='group-item')[:8]:
                        title_elem = item.find('h4') or item.find('strong')
                        link_elem = item.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            link = link_elem['href']
                            results.append({'title': title, 'link': link})
        except Exception as e:
            logger.error(f"Erreur telegram-group.com: {e}")
        return results
    
    async def search_direct_telegram(self, keyword):
        """Recherche directe sur Telegram"""
        results = []
        try:
            # Essayer des variations du mot-clé
            variations = [keyword, keyword.lower(), keyword.replace(' ', '_')]
            
            for variation in variations:
                url = f"https://t.me/{variation}"
                async with self.session.get(url, allow_redirects=False) as response:
                    if response.status in [200, 301, 302]:
                        results.append({
                            'title': f"@{variation}",
                            'link': url
                        })
                        if len(results) >= 3:
                            break
        except Exception as e:
            logger.error(f"Erreur recherche directe: {e}")
        return results
    
    async def comprehensive_search(self, keyword):
        """Effectue une recherche complète sur toutes les sources"""
        await self.create_session()
        
        # Lancer toutes les recherches en parallèle
        tasks = [
            self.search_tlgrm_eu(keyword),
            self.search_tgstat(keyword),
            self.search_telegram_group_com(keyword),
            self.search_direct_telegram(keyword)
        ]
        
        try:
            results_lists = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Combiner tous les résultats
            all_results = []
            for results in results_lists:
                if isinstance(results, list):
                    all_results.extend(results)
            
            # Supprimer les doublons et limiter à 20
            unique_results = []
            seen_links = set()
            
            for result in all_results:
                if result['link'] not in seen_links and len(unique_results) < 20:
                    unique_results.append(result)
                    seen_links.add(result['link'])
            
            return unique_results
            
        except Exception as e:
            logger.error(f"Erreur recherche globale: {e}")
            return []
    
    async def close_session(self):
        """Ferme la session HTTP"""
        if self.session:
            await self.session.close()

# Instance globale du chercheur
searcher = TelegramGroupSearcher()

# Flask app pour les webhooks
app = Flask(__name__)

# Application Telegram globale
telegram_app = None

@app.route('/')
def health_check():
    """Health check pour Render"""
    return "Bot Telegram is running! 🚀"

@app.route(f'/{os.getenv("TELEGRAM_BOT_TOKEN", "")}', methods=['POST'])
def webhook():
    """Gestionnaire de webhook Telegram"""
    try:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        # Traiter l'update de manière asynchrone
        asyncio.create_task(telegram_app.process_update(update))
        return "OK"
    except Exception as e:
        logger.error(f"Erreur webhook: {e}")
        return "Error", 500

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /start"""
    welcome_msg = """
🤖 **Bot de Recherche de Groupes Telegram**

Utilisez la commande `/search <mot-clé>` pour rechercher des groupes.

**Exemples:**
• `/search musique`
• `/search crypto`
• `/search france`
• `/search gaming`

Je vais chercher dans plusieurs sources et vous donner jusqu'à 20 résultats !
    """
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

async def search_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /search"""
    if not context.args:
        await update.message.reply_text(
            "❌ Veuillez spécifier un mot-clé.\nExemple: `/search musique`",
            parse_mode='Markdown'
        )
        return
    
    keyword = ' '.join(context.args)
    
    # Message de chargement
    loading_msg = await update.message.reply_text(
        f"🔍 Recherche en cours pour: **{keyword}**\nVeuillez patienter...",
        parse_mode='Markdown'
    )
    
    try:
        # Effectuer la recherche
        results = await searcher.comprehensive_search(keyword)
        
        if not results:
            await loading_msg.edit_text(
                f"❌ Aucun groupe trouvé pour: **{keyword}**\n"
                "Essayez avec d'autres mots-clés.",
                parse_mode='Markdown'
            )
            return
        
        # Formater les résultats
        response = f"🎯 **Résultats pour: {keyword}**\n"
        response += f"📊 **{len(results)} groupe(s) trouvé(s)**\n\n"
        
        for i, result in enumerate(results, 1):
            title = result['title'][:50] + "..." if len(result['title']) > 50 else result['title']
            response += f"**{i}.** {title}\n🔗 {result['link']}\n\n"
        
        # Diviser en plusieurs messages si trop long
        if len(response) > 4000:
            # Envoyer par blocs
            chunks = []
            current_chunk = f"🎯 **Résultats pour: {keyword}**\n📊 **{len(results)} groupe(s) trouvé(s)**\n\n"
            
            for i, result in enumerate(results, 1):
                title = result['title'][:50] + "..." if len(result['title']) > 50 else result['title']
                item = f"**{i}.** {title}\n🔗 {result['link']}\n\n"
                
                if len(current_chunk + item) > 4000:
                    chunks.append(current_chunk)
                    current_chunk = item
                else:
                    current_chunk += item
            
            if current_chunk:
                chunks.append(current_chunk)
            
            # Envoyer le premier chunk en éditant le message de chargement
            await loading_msg.edit_text(chunks[0], parse_mode='Markdown')
            
            # Envoyer les chunks suivants
            for chunk in chunks[1:]:
                await update.message.reply_text(chunk, parse_mode='Markdown')
        else:
            await loading_msg.edit_text(response, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Erreur lors de la recherche: {e}")
        await loading_msg.edit_text(
            f"❌ Erreur lors de la recherche pour: **{keyword}**\n"
            "Veuillez réessayer plus tard.",
            parse_mode='Markdown'
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /help"""
    help_text = """
🆘 **Aide - Bot de Recherche Telegram**

**Commandes disponibles:**
• `/start` - Démarrer le bot
• `/search <mot-clé>` - Rechercher des groupes
• `/help` - Afficher cette aide

**Exemples de recherche:**
• `/search musique` - Groupes de musique
• `/search crypto bitcoin` - Groupes crypto
• `/search france paris` - Groupes français
• `/search gaming fortnite` - Groupes gaming

**Fonctionnalités:**
✅ Recherche sur plusieurs sources
✅ Jusqu'à 20 résultats par recherche
✅ Liens directs vers les groupes
✅ Recherche rapide et efficace

**Support:** Contactez le développeur en cas de problème.
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

def main():
    """Fonction principale"""
    global telegram_app
    
    # Récupérer le token depuis les variables d'environnement
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN non trouvé dans les variables d'environnement")
        return
    
    # Créer l'application Telegram
    telegram_app = Application.builder().token(TOKEN).build()
    
    # Ajouter les gestionnaires de commandes
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("search", search_groups))
    telegram_app.add_handler(CommandHandler("help", help_command))
    
    # Initialiser l'application (important pour les webhooks)
    asyncio.run(telegram_app.initialize())
    
    logger.info("Bot configuré et prêt!")

def setup_webhook():
    """Configure le webhook Telegram"""
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    RENDER_URL = os.getenv('RENDER_EXTERNAL_URL', 'https://your-app-name.onrender.com')
    
    if TOKEN and RENDER_URL:
        webhook_url = f"{RENDER_URL}/{TOKEN}"
        logger.info(f"Webhook URL configurée: {webhook_url}")
        
        # Configurer le webhook (sera fait automatiquement au premier déploiement)
        import requests
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/setWebhook",
                json={"url": webhook_url}
            )
            if response.status_code == 200:
                logger.info("Webhook configuré avec succès!")
            else:
                logger.error(f"Erreur configuration webhook: {response.text}")
        except Exception as e:
            logger.error(f"Erreur lors de la configuration du webhook: {e}")

if __name__ == '__main__':
    # Initialiser le bot Telegram
    main()
    
    # Configurer le webhook
    setup_webhook()
    
    # Démarrer l'application Flask
    PORT = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=PORT, debug=False)
