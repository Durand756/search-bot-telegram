import os
import asyncio
import aiohttp
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from urllib.parse import quote, urljoin
import time
import signal
import sys

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
        """Crée une session HTTP robuste"""
        if not self.session or self.session.closed:
            connector = aiohttp.TCPConnector(
                limit=20,
                limit_per_host=10,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=30,
                enable_cleanup_closed=True
            )
            timeout = aiohttp.ClientTimeout(total=25, connect=10)
            
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'User-Agent': self.ua.random,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
            )
    
    async def search_tlgrm_eu(self, keyword):
        """Recherche sur tlgrm.eu"""
        results = []
        try:
            await self.create_session()
            url = f"https://tlgrm.eu/search?q={quote(keyword)}"
            logger.info(f"Recherche sur tlgrm.eu: {url}")
            
            async with self.session.get(url) as response:
                logger.info(f"Status tlgrm.eu: {response.status}")
                if response.status == 200:
                    text = await response.text()
                    soup = BeautifulSoup(text, 'html.parser')
                    
                    # Plusieurs sélecteurs possibles
                    for selector in ['div.result-item', 'div.search-result', '.channel-card', 'div.group-item']:
                        items = soup.select(selector)
                        if items:
                            logger.info(f"Trouvé {len(items)} items avec {selector}")
                            break
                    
                    for item in items[:8]:
                        try:
                            # Chercher le titre
                            title_elem = (item.find('h3') or item.find('h4') or 
                                        item.find('span', class_='title') or 
                                        item.find('a') or item.find('strong'))
                            
                            # Chercher le lien
                            link_elem = item.find('a', href=True)
                            
                            if title_elem and link_elem:
                                title = title_elem.get_text(strip=True)
                                link = link_elem['href']
                                
                                if not link.startswith('http'):
                                    link = urljoin('https://tlgrm.eu', link)
                                
                                if title and len(title) > 2:
                                    results.append({
                                        'title': title,
                                        'link': link,
                                        'source': 'tlgrm.eu'
                                    })
                        except Exception as e:
                            logger.warning(f"Erreur item tlgrm.eu: {e}")
                            continue
                            
        except Exception as e:
            logger.error(f"Erreur tlgrm.eu: {e}")
        
        logger.info(f"tlgrm.eu: {len(results)} résultats")
        return results
    
    async def search_tgstat(self, keyword):
        """Recherche sur tgstat.com"""
        results = []
        try:
            await self.create_session()
            url = f"https://tgstat.com/search?q={quote(keyword)}"
            logger.info(f"Recherche sur tgstat: {url}")
            
            async with self.session.get(url) as response:
                logger.info(f"Status tgstat: {response.status}")
                if response.status == 200:
                    text = await response.text()
                    soup = BeautifulSoup(text, 'html.parser')
                    
                    # Chercher les éléments de résultat
                    for selector in ['div.channel-card', 'div.search-result', '.result-item']:
                        items = soup.select(selector)
                        if items:
                            logger.info(f"Trouvé {len(items)} items tgstat avec {selector}")
                            break
                    
                    for item in items[:8]:
                        try:
                            title_elem = (item.find('div', class_='channel-title') or 
                                        item.find('h3') or item.find('a'))
                            link_elem = item.find('a', href=True)
                            
                            if title_elem and link_elem:
                                title = title_elem.get_text(strip=True)
                                link = link_elem['href']
                                
                                if not link.startswith('http'):
                                    link = urljoin('https://tgstat.com', link)
                                
                                if title and len(title) > 2:
                                    results.append({
                                        'title': title,
                                        'link': link,
                                        'source': 'tgstat'
                                    })
                        except Exception as e:
                            logger.warning(f"Erreur item tgstat: {e}")
                            continue
                            
        except Exception as e:
            logger.error(f"Erreur tgstat: {e}")
        
        logger.info(f"tgstat: {len(results)} résultats")
        return results
    
    async def search_direct_telegram(self, keyword):
        """Recherche directe sur Telegram"""
        results = []
        try:
            await self.create_session()
            
            # Créer des variations intelligentes
            base_variations = [
                keyword.lower().replace(' ', ''),
                keyword.lower().replace(' ', '_'),
                keyword.replace(' ', ''),
                f"{keyword.lower()}group",
                f"{keyword.lower()}chat",
                keyword.lower()
            ]
            
            # Nettoyer et limiter les variations
            variations = []
            for v in base_variations:
                if len(v) >= 3 and v.isalnum() or '_' in v:
                    variations.append(v)
            
            variations = list(set(variations))[:6]  # Max 6 variations
            logger.info(f"Variations directes: {variations}")
            
            for variation in variations:
                try:
                    url = f"https://t.me/{variation}"
                    async with self.session.head(url, allow_redirects=True) as response:
                        if response.status == 200:
                            results.append({
                                'title': f"@{variation}",
                                'link': url,
                                'source': 'direct'
                            })
                            logger.info(f"Trouvé direct: @{variation}")
                            
                except Exception:
                    continue
                    
                if len(results) >= 4:
                    break
                    
        except Exception as e:
            logger.error(f"Erreur recherche directe: {e}")
        
        logger.info(f"Direct: {len(results)} résultats")
        return results
    
    async def search_lyzem(self, keyword):
        """Recherche sur lyzem.com"""
        results = []
        try:
            await self.create_session()
            url = f"https://lyzem.com/search?q={quote(keyword)}"
            logger.info(f"Recherche sur lyzem: {url}")
            
            async with self.session.get(url) as response:
                if response.status == 200:
                    text = await response.text()
                    soup = BeautifulSoup(text, 'html.parser')
                    
                    # Recherche de liens Telegram
                    links = soup.find_all('a', href=True)
                    for link in links:
                        href = link['href']
                        if 't.me/' in href and len(results) < 5:
                            title = link.get_text(strip=True)
                            if not title:
                                title = href.split('/')[-1]
                            
                            if title and len(title) > 2:
                                results.append({
                                    'title': title[:50],
                                    'link': href if href.startswith('http') else f"https://t.me/{href.split('/')[-1]}",
                                    'source': 'lyzem'
                                })
                                
        except Exception as e:
            logger.error(f"Erreur lyzem: {e}")
        
        logger.info(f"lyzem: {len(results)} résultats")
        return results
    
    async def comprehensive_search(self, keyword):
        """Recherche complète avec toutes les sources"""
        logger.info(f"=== Début recherche pour: '{keyword}' ===")
        
        try:
            # Lancer toutes les recherches en parallèle avec timeout
            search_tasks = [
                asyncio.wait_for(self.search_tlgrm_eu(keyword), timeout=20),
                asyncio.wait_for(self.search_tgstat(keyword), timeout=20),
                asyncio.wait_for(self.search_direct_telegram(keyword), timeout=15),
                asyncio.wait_for(self.search_lyzem(keyword), timeout=20)
            ]
            
            results_lists = await asyncio.gather(*search_tasks, return_exceptions=True)
            
            # Combiner tous les résultats
            all_results = []
            for i, results in enumerate(results_lists):
                if isinstance(results, list):
                    all_results.extend(results)
                    logger.info(f"Source {i}: {len(results)} résultats")
                elif isinstance(results, Exception):
                    logger.warning(f"Source {i} a échoué: {results}")
            
            # Supprimer les doublons
            unique_results = []
            seen_links = set()
            seen_titles = set()
            
            for result in all_results:
                link_key = result['link'].lower().rstrip('/')
                title_key = result['title'].lower().strip()
                
                if link_key not in seen_links and title_key not in seen_titles:
                    unique_results.append(result)
                    seen_links.add(link_key)
                    seen_titles.add(title_key)
                    
                    if len(unique_results) >= 20:
                        break
            
            logger.info(f"=== Résultat final: {len(unique_results)} résultats uniques ===")
            return unique_results
            
        except Exception as e:
            logger.error(f"Erreur recherche globale: {e}")
            return []
    
    async def close_session(self):
        """Ferme la session HTTP"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("Session HTTP fermée")

# Instance globale
searcher = TelegramGroupSearcher()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /start"""
    user = update.effective_user
    logger.info(f"Commande /start par {user.first_name} (ID: {user.id})")
    
    welcome_msg = """🤖 **Bot de Recherche de Groupes Telegram**

Salut ! Je peux t'aider à trouver des groupes Telegram sur n'importe quel sujet.

**🔍 Comment utiliser :**
`/search <ton mot-clé>`

**💡 Exemples :**
• `/search musique` 
• `/search crypto bitcoin`
• `/search france`
• `/search gaming esport`
• `/search technologie`

**✨ Fonctionnalités :**
✅ Recherche sur plusieurs sources
✅ Résultats en temps réel
✅ Liens directs vers les groupes
✅ Jusqu'à 20 résultats par recherche

Tape `/help` pour plus d'infos !"""

    try:
        await update.message.reply_text(welcome_msg, parse_mode='Markdown')
        logger.info(f"Message de bienvenue envoyé à {user.first_name}")
    except Exception as e:
        logger.error(f"Erreur envoi message start: {e}")
        await update.message.reply_text("🤖 Bot démarré ! Utilise /search <mot-clé> pour chercher des groupes.")

async def search_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /search"""
    user = update.effective_user
    logger.info(f"Commande /search par {user.first_name} (ID: {user.id})")
    
    try:
        # Vérifier les arguments
        if not context.args:
            await update.message.reply_text(
                "❌ **Utilisation incorrecte**\n\n"
                "**Format correct :** `/search <mot-clé>`\n\n"
                "**Exemples :**\n"
                "• `/search musique`\n"
                "• `/search crypto`\n"
                "• `/search france`\n"
                "• `/search gaming`",
                parse_mode='Markdown'
            )
            return
        
        keyword = ' '.join(context.args).strip()
        logger.info(f"Recherche demandée: '{keyword}' par {user.first_name}")
        
        if len(keyword) < 2:
            await update.message.reply_text("❌ Le mot-clé doit contenir au moins 2 caractères.")
            return
        
        if len(keyword) > 50:
            await update.message.reply_text("❌ Le mot-clé est trop long (max 50 caractères).")
            return
        
        # Message de chargement
        loading_msg = await update.message.reply_text(
            f"🔍 **Recherche en cours...**\n\n"
            f"**Mot-clé :** `{keyword}`\n"
            f"**Statut :** Recherche sur plusieurs sources...\n"
            f"⏳ Patiente quelques secondes...",
            parse_mode='Markdown'
        )
        
        start_time = time.time()
        
        # Effectuer la recherche
        results = await searcher.comprehensive_search(keyword)
        
        search_time = round(time.time() - start_time, 2)
        logger.info(f"Recherche terminée en {search_time}s: {len(results)} résultats")
        
        # Traiter les résultats
        if not results:
            await loading_msg.edit_text(
                f"❌ **Aucun résultat trouvé**\n\n"
                f"**Mot-clé :** `{keyword}`\n"
                f"**Temps de recherche :** {search_time}s\n\n"
                f"💡 **Suggestions :**\n"
                f"• Essaie des mots-clés plus généraux\n"
                f"• Utilise des termes en anglais\n"
                f"• Vérifie l'orthographe\n"
                f"• Essaie des synonymes",
                parse_mode='Markdown'
            )
            return
        
        # Formater la réponse
        header = (
            f"🎯 **Résultats pour :** `{keyword}`\n"
            f"📊 **{len(results)} groupe(s) trouvé(s)** en {search_time}s\n\n"
        )
        
        # Grouper par source pour un meilleur affichage
        by_source = {}
        for result in results:
            source = result.get('source', 'unknown')
            if source not in by_source:
                by_source[source] = []
            by_source[source].append(result)
        
        content = ""
        counter = 1
        
        # Afficher les résultats par source
        source_emojis = {
            'direct': '🔗',
            'tlgrm.eu': '🌐',
            'tgstat': '📊',
            'lyzem': '🔍'
        }
        
        for source, source_results in by_source.items():
            emoji = source_emojis.get(source, '📱')
            
            for result in source_results:
                title = result['title']
                if len(title) > 40:
                    title = title[:37] + "..."
                
                content += f"{emoji} **{counter}.** {title}\n"
                content += f"     {result['link']}\n\n"
                counter += 1
        
        footer = "💡 **Clique sur les liens pour rejoindre les groupes !**"
        full_response = header + content + footer
        
        # Gérer les messages trop longs
        if len(full_response) > 4000:
            # Diviser en chunks
            await loading_msg.edit_text(header, parse_mode='Markdown')
            
            current_chunk = ""
            chunk_counter = 1
            
            for source, source_results in by_source.items():
                emoji = source_emojis.get(source, '📱')
                
                for result in source_results:
                    title = result['title']
                    if len(title) > 40:
                        title = title[:37] + "..."
                    
                    item = f"{emoji} **{chunk_counter}.** {title}\n     {result['link']}\n\n"
                    
                    if len(current_chunk + item) > 3800:
                        if current_chunk:
                            await update.message.reply_text(current_chunk, parse_mode='Markdown')
                        current_chunk = item
                    else:
                        current_chunk += item
                    
                    chunk_counter += 1
            
            if current_chunk:
                await update.message.reply_text(current_chunk + footer, parse_mode='Markdown')
        else:
            await loading_msg.edit_text(full_response, parse_mode='Markdown')
        
        logger.info(f"Résultats envoyés à {user.first_name}")
        
    except Exception as e:
        logger.error(f"Erreur dans search_groups: {e}")
        try:
            await update.message.reply_text(
                f"❌ **Erreur de recherche**\n\n"
                f"Une erreur s'est produite pendant la recherche.\n"
                f"Réessaie dans quelques instants.\n\n"
                f"Si le problème persist, contacte l'admin.",
                parse_mode='Markdown'
            )
        except Exception as e2:
            logger.error(f"Erreur envoi message d'erreur: {e2}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /help"""
    user = update.effective_user
    logger.info(f"Commande /help par {user.first_name}")
    
    help_text = """🆘 **Guide d'utilisation**

**📋 Commandes disponibles :**
• `/start` - Démarrer le bot
• `/search <mot-clé>` - Rechercher des groupes
• `/help` - Afficher cette aide

**🔍 Exemples de recherche :**
• `/search musique rock` - Groupes de musique rock
• `/search crypto bitcoin` - Groupes crypto/Bitcoin
• `/search france paris` - Groupes français/parisiens
• `/search gaming fortnite` - Groupes gaming
• `/search tech programming` - Groupes tech/dev
• `/search anime manga` - Groupes anime/manga

**⚡ Fonctionnalités :**
✅ Recherche simultanée sur 4+ sources
✅ Résultats en temps réel (5-15 secondes)
✅ Jusqu'à 20 groupes par recherche
✅ Liens directs cliquables
✅ Recherche en français et anglais

**💡 Conseils pour de meilleurs résultats :**
• Utilise des mots-clés précis mais pas trop spécifiques
• Combine plusieurs mots pour affiner la recherche
• Essaie en anglais pour plus de résultats internationaux
• Utilise des termes populaires (crypto, gaming, music, etc.)

**🔧 En cas de problème :**
• Vérifie l'orthographe de tes mots-clés
• Essaie des synonymes ou termes similaires
• Attends quelques secondes entre les recherches
• Contacte l'admin si ça ne fonctionne toujours pas

**🚀 Prêt à chercher ? Utilise `/search <ton-mot-clé>` !**"""

    try:
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Erreur commande help: {e}")
        await update.message.reply_text("📋 Commandes: /start /search /help")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestionnaire d'erreurs global"""
    logger.error(f"Erreur non gérée: {context.error}")
    
    if update and update.message:
        try:
            await update.message.reply_text(
                "❌ Une erreur inattendue s'est produite. Réessaie plus tard."
            )
        except Exception:
            pass

def signal_handler(signum, frame):
    """Gestionnaire de signaux pour arrêt propre"""
    logger.info(f"Signal {signum} reçu, arrêt du bot...")
    sys.exit(0)

async def main():
    """Fonction principale avec polling"""
    logger.info("🚀 Démarrage du bot en mode polling...")
    
    # Récupérer le token
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN non trouvé dans les variables d'environnement !")
        return
    
    # Configurer les signaux
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Créer l'application
        application = (
            Application.builder()
            .token(TOKEN)
            .read_timeout(30)
            .write_timeout(30)
            .connect_timeout(30)
            .pool_timeout(30)
            .get_updates_read_timeout(30)
            .get_updates_write_timeout(30)
            .get_updates_connect_timeout(30)
            .get_updates_pool_timeout(30)
            .build()
        )
        
        # Ajouter les handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("search", search_groups))
        application.add_handler(CommandHandler("help", help_command))
        application.add_error_handler(error_handler)
        
        logger.info("✅ Handlers ajoutés")
        
        # Test de connexion
        try:
            bot_info = await application.bot.get_me()
            logger.info(f"✅ Bot connecté: @{bot_info.username} ({bot_info.first_name})")
        except Exception as e:
            logger.error(f"❌ Erreur de connexion au bot: {e}")
            return
        
        # Démarrer le polling
        logger.info("🔄 Démarrage du polling...")
        await application.run_polling(
            poll_interval=1.0,
            timeout=20,
            bootstrap_retries=5,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30,
            pool_timeout=30,
            drop_pending_updates=True
        )
        
    except KeyboardInterrupt:
        logger.info("🛑 Arrêt demandé par l'utilisateur")
    except Exception as e:
        logger.error(f"❌ Erreur critique: {e}")
    finally:
        # Nettoyer
        await searcher.close_session()
        logger.info("🧹 Nettoyage terminé")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot arrêté")
    except Exception as e:
        logger.error(f"❌ Erreur au démarrage: {e}")
