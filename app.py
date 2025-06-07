import os
import asyncio
import aiohttp
import logging
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from urllib.parse import quote, urljoin
import threading
import time

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
        
    async def create_session(self):
        """Crée une session HTTP avec headers personnalisés"""
        if not self.session or self.session.closed:
            connector = aiohttp.TCPConnector(
                limit=10,
                limit_per_host=5,
                ttl_dns_cache=300,
                use_dns_cache=True,
            )
            timeout = aiohttp.ClientTimeout(total=20, connect=10)
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
        """Recherche sur tlgrm.eu avec gestion d'erreurs améliorée"""
        results = []
        try:
            await self.create_session()
            url = f"https://tlgrm.eu/search?q={quote(keyword)}"
            
            async with self.session.get(url) as response:
                if response.status == 200:
                    text = await response.text()
                    soup = BeautifulSoup(text, 'html.parser')
                    
                    # Chercher différents sélecteurs possibles
                    selectors = [
                        'div.result-item',
                        'div.search-result',
                        'div.channel-item',
                        '.result'
                    ]
                    
                    items_found = []
                    for selector in selectors:
                        items_found = soup.select(selector)
                        if items_found:
                            break
                    
                    for item in items_found[:8]:
                        title_elem = (item.find('h3') or 
                                    item.find('h4') or 
                                    item.find('a') or 
                                    item.find('span', class_='title'))
                        
                        link_elem = item.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            link = link_elem['href']
                            
                            if not link.startswith('http'):
                                link = urljoin('https://tlgrm.eu', link)
                            
                            if title and link:
                                results.append({'title': title, 'link': link, 'source': 'tlgrm.eu'})
                                
        except asyncio.TimeoutError:
            logger.warning("Timeout sur tlgrm.eu")
        except Exception as e:
            logger.error(f"Erreur tlgrm.eu: {e}")
        
        return results
    
    async def search_telegram_direct(self, keyword):
        """Recherche directe sur Telegram avec variations intelligentes"""
        results = []
        try:
            await self.create_session()
            
            # Créer des variations du mot-clé
            variations = [
                keyword.lower().replace(' ', ''),
                keyword.lower().replace(' ', '_'),
                keyword.replace(' ', ''),
                f"{keyword}group",
                f"{keyword}chat",
                keyword.lower()
            ]
            
            # Supprimer les doublons
            variations = list(set(variations))
            
            for variation in variations[:5]:  # Limiter à 5 variations
                if len(variation) > 2:  # Éviter les mots trop courts
                    url = f"https://t.me/{variation}"
                    try:
                        async with self.session.get(url, allow_redirects=False) as response:
                            if response.status in [200, 301, 302]:
                                results.append({
                                    'title': f"@{variation}",
                                    'link': url,
                                    'source': 'direct'
                                })
                                
                    except Exception:
                        continue  # Ignorer les erreurs individuelles
                        
                    if len(results) >= 3:
                        break
                        
        except Exception as e:
            logger.error(f"Erreur recherche directe: {e}")
            
        return results
    
    async def search_alternative_sources(self, keyword):
        """Recherche sur des sources alternatives"""
        results = []
        try:
            await self.create_session()
            
            # Sources alternatives simples
            search_urls = [
                f"https://lyzem.com/search?q={quote(keyword)}",
                f"https://telegramic.org/search?q={quote(keyword)}"
            ]
            
            for url in search_urls:
                try:
                    async with self.session.get(url) as response:
                        if response.status == 200:
                            text = await response.text()
                            soup = BeautifulSoup(text, 'html.parser')
                            
                            # Recherche générique de liens Telegram
                            links = soup.find_all('a', href=True)
                            for link in links:
                                href = link['href']
                                if 't.me/' in href and len(results) < 5:
                                    title = link.get_text(strip=True) or href.split('/')[-1]
                                    if title and len(title) > 2:
                                        results.append({
                                            'title': title[:50],
                                            'link': href,
                                            'source': 'alternative'
                                        })
                                        
                except Exception:
                    continue
                    
        except Exception as e:
            logger.error(f"Erreur sources alternatives: {e}")
            
        return results
    
    async def comprehensive_search(self, keyword):
        """Effectue une recherche complète avec timeout et gestion d'erreurs"""
        await self.create_session()
        
        try:
            # Lancer les recherches avec timeout
            tasks = [
                asyncio.wait_for(self.search_tlgrm_eu(keyword), timeout=15),
                asyncio.wait_for(self.search_telegram_direct(keyword), timeout=10),
                asyncio.wait_for(self.search_alternative_sources(keyword), timeout=15)
            ]
            
            results_lists = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Combiner tous les résultats valides
            all_results = []
            for results in results_lists:
                if isinstance(results, list):
                    all_results.extend(results)
                elif isinstance(results, Exception):
                    logger.warning(f"Une recherche a échoué: {results}")
            
            # Supprimer les doublons basés sur le lien
            unique_results = []
            seen_links = set()
            
            for result in all_results:
                link_key = result['link'].lower().strip('/')
                if link_key not in seen_links and len(unique_results) < 15:
                    unique_results.append(result)
                    seen_links.add(link_key)
            
            logger.info(f"Trouvé {len(unique_results)} résultats pour '{keyword}'")
            return unique_results
            
        except Exception as e:
            logger.error(f"Erreur recherche globale: {e}")
            return []
    
    async def close_session(self):
        """Ferme la session HTTP proprement"""
        if self.session and not self.session.closed:
            await self.session.close()

# Instance globale du chercheur
searcher = TelegramGroupSearcher()

# Flask app pour les webhooks
app = Flask(__name__)

# Variables globales
telegram_app = None
bot_instance = None

@app.route('/')
def health_check():
    """Health check pour Render"""
    return jsonify({
        "status": "running",
        "bot": "Telegram Group Searcher",
        "version": "2.0"
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """Gestionnaire de webhook Telegram amélioré"""
    try:
        json_data = request.get_json(force=True)
        if not json_data:
            return "No data", 400
            
        update = Update.de_json(json_data, bot_instance)
        
        # Traiter l'update dans un thread séparé pour éviter les timeouts
        def process_update():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(telegram_app.process_update(update))
                loop.close()
            except Exception as e:
                logger.error(f"Erreur traitement update: {e}")
        
        threading.Thread(target=process_update, daemon=True).start()
        return "OK"
        
    except Exception as e:
        logger.error(f"Erreur webhook: {e}")
        return "Error", 500

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /start améliorée"""
    welcome_msg = """🤖 **Bot de Recherche de Groupes Telegram**

Bienvenue ! Je peux vous aider à trouver des groupes Telegram.

**Commandes disponibles:**
• `/search <mot-clé>` - Rechercher des groupes
• `/help` - Afficher l'aide complète

**Exemples:**
• `/search musique`
• `/search crypto bitcoin`
• `/search france`
• `/search gaming`

✨ Je recherche sur plusieurs sources pour vous donner les meilleurs résultats !"""

    try:
        await update.message.reply_text(welcome_msg, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Erreur commande start: {e}")
        await update.message.reply_text("Bot démarré ! Utilisez /search <mot-clé> pour rechercher.")

async def search_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /search améliorée avec gestion d'erreurs robuste"""
    try:
        if not context.args:
            await update.message.reply_text(
                "❌ **Utilisation:** `/search <mot-clé>`\n\n"
                "**Exemples:**\n"
                "• `/search musique`\n"
                "• `/search crypto`\n"
                "• `/search france`",
                parse_mode='Markdown'
            )
            return
        
        keyword = ' '.join(context.args).strip()
        
        if len(keyword) < 2:
            await update.message.reply_text("❌ Le mot-clé doit contenir au moins 2 caractères.")
            return
        
        # Message de chargement
        loading_msg = await update.message.reply_text(
            f"🔍 **Recherche en cours...**\n"
            f"Mot-clé: `{keyword}`\n"
            f"⏳ Cela peut prendre quelques secondes...",
            parse_mode='Markdown'
        )
        
        # Effectuer la recherche
        results = await searcher.comprehensive_search(keyword)
        
        if not results:
            await loading_msg.edit_text(
                f"❌ **Aucun résultat trouvé**\n\n"
                f"Mot-clé recherché: `{keyword}`\n\n"
                f"💡 **Suggestions:**\n"
                f"• Essayez des mots-clés plus généraux\n"
                f"• Utilisez des termes en anglais\n"
                f"• Vérifiez l'orthographe",
                parse_mode='Markdown'
            )
            return
        
        # Formater les résultats
        response = f"🎯 **Résultats pour:** `{keyword}`\n"
        response += f"📊 **{len(results)} groupe(s) trouvé(s)**\n\n"
        
        for i, result in enumerate(results, 1):
            title = result['title']
            if len(title) > 45:
                title = title[:42] + "..."
            
            source_emoji = "🔗" if result.get('source') == 'direct' else "🌐"
            response += f"{source_emoji} **{i}.** {title}\n"
            response += f"   {result['link']}\n\n"
        
        response += "💡 Cliquez sur les liens pour rejoindre les groupes !"
        
        # Gérer les messages trop longs
        if len(response) > 4000:
            # Diviser en chunks
            header = f"🎯 **Résultats pour:** `{keyword}`\n📊 **{len(results)} groupe(s) trouvé(s)**\n\n"
            
            chunks = [header]
            current_chunk = ""
            
            for i, result in enumerate(results, 1):
                title = result['title']
                if len(title) > 45:
                    title = title[:42] + "..."
                
                source_emoji = "🔗" if result.get('source') == 'direct' else "🌐"
                item = f"{source_emoji} **{i}.** {title}\n   {result['link']}\n\n"
                
                if len(current_chunk + item) > 3500:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = item
                else:
                    current_chunk += item
            
            if current_chunk:
                chunks.append(current_chunk + "💡 Cliquez sur les liens pour rejoindre les groupes !")
            
            # Envoyer les chunks
            await loading_msg.edit_text(chunks[0], parse_mode='Markdown')
            
            for chunk in chunks[1:]:
                await update.message.reply_text(chunk, parse_mode='Markdown')
        else:
            await loading_msg.edit_text(response, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Erreur lors de la recherche: {e}")
        try:
            await update.message.reply_text(
                f"❌ **Erreur lors de la recherche**\n\n"
                f"Une erreur s'est produite. Veuillez réessayer dans quelques instants.\n\n"
                f"Si le problème persiste, contactez l'administrateur.",
                parse_mode='Markdown'
            )
        except Exception:
            await update.message.reply_text("❌ Erreur lors de la recherche. Veuillez réessayer.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /help"""
    help_text = """🆘 **Aide - Bot de Recherche Telegram**

**📋 Commandes disponibles:**
• `/start` - Démarrer le bot
• `/search <mot-clé>` - Rechercher des groupes
• `/help` - Afficher cette aide

**🔍 Exemples de recherche:**
• `/search musique` - Groupes de musique
• `/search crypto bitcoin` - Groupes crypto
• `/search france paris` - Groupes français
• `/search gaming` - Groupes de jeux
• `/search tech programming` - Groupes tech

**⚡ Fonctionnalités:**
✅ Recherche sur plusieurs sources
✅ Résultats rapides et précis
✅ Liens directs vers les groupes
✅ Recherche en français et anglais

**💡 Conseils:**
• Utilisez des mots-clés précis
• Essayez en anglais pour plus de résultats
• Combinez plusieurs mots-clés

**🔧 Support:** En cas de problème, contactez l'administrateur."""

    try:
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Erreur commande help: {e}")
        await update.message.reply_text("Aide disponible : /start /search /help")

async def setup_application():
    """Configure l'application Telegram de manière asynchrone"""
    global telegram_app, bot_instance
    
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN non trouvé !")
        return False
    
    try:
        # Créer l'application avec une configuration robuste
        telegram_app = (
            Application.builder()
            .token(TOKEN)
            .read_timeout(30)
            .write_timeout(30)
            .connect_timeout(30)
            .pool_timeout(30)
            .build()
        )
        
        bot_instance = telegram_app.bot
        
        # Ajouter les gestionnaires
        telegram_app.add_handler(CommandHandler("start", start))
        telegram_app.add_handler(CommandHandler("search", search_groups))
        telegram_app.add_handler(CommandHandler("help", help_command))
        
        # Initialiser
        await telegram_app.initialize()
        await telegram_app.start()
        
        logger.info("✅ Application Telegram configurée avec succès !")
        return True
        
    except Exception as e:
        logger.error(f"❌ Erreur configuration Telegram: {e}")
        return False

def setup_webhook():
    """Configure le webhook Telegram"""
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    WEBHOOK_URL = os.getenv('WEBHOOK_URL')
    
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN manquant")
        return
    
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL non défini, webhook non configuré")
        return
    
    try:
        import requests
        
        webhook_url = f"{WEBHOOK_URL}/webhook"
        api_url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
        
        response = requests.post(
            api_url,
            json={"url": webhook_url},
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                logger.info(f"✅ Webhook configuré: {webhook_url}")
            else:
                logger.error(f"❌ Erreur webhook: {result}")
        else:
            logger.error(f"❌ Erreur HTTP webhook: {response.status_code}")
            
    except Exception as e:
        logger.error(f"❌ Exception webhook: {e}")

async def main_async():
    """Fonction principale asynchrone"""
    logger.info("🚀 Démarrage du bot...")
    
    success = await setup_application()
    if not success:
        logger.error("❌ Échec de la configuration")
        return
    
    setup_webhook()
    logger.info("✅ Bot prêt !")

def main():
    """Point d'entrée principal"""
    try:
        asyncio.run(main_async())
    except Exception as e:
        logger.error(f"❌ Erreur critique: {e}")

if __name__ == '__main__':
    # Configurer le bot
    main()
    
    # Démarrer Flask
    PORT = int(os.environ.get('PORT', 8000))
    logger.info(f"🌐 Démarrage Flask sur le port {PORT}")
    
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,
        threaded=True
    )
