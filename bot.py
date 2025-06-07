import os
import asyncio
import aiohttp
import logging
from datetime import datetime
from typing import List, Dict, Optional
import re
from urllib.parse import quote_plus, urljoin
from bs4 import BeautifulSoup
import random
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

# Configuration du logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class TelegramGroupSearcher:
    def __init__(self):
        self.session = None
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        ]
        
        # Sites de recherche multiples pour plus de résultats
        self.search_sources = [
            {
                'name': 'TLGRM.eu',
                'base_url': 'https://tlgrm.eu/channels',
                'search_pattern': '?search={query}&sort=members',
                'parser': self._parse_tlgrm
            },
            {
                'name': 'TelegramChannels.me',
                'base_url': 'https://telegramchannels.me/channels',
                'search_pattern': '?q={query}',
                'parser': self._parse_telegram_channels
            },
            {
                'name': 'Telegram-Store',
                'base_url': 'https://telegram-store.com/search',
                'search_pattern': '?q={query}',
                'parser': self._parse_telegram_store
            }
        ]

    async def init_session(self):
        """Initialise la session HTTP avec des headers optimisés"""
        connector = aiohttp.TCPConnector(limit=30, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=15)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
        )

    async def close_session(self):
        """Ferme la session HTTP"""
        if self.session:
            await self.session.close()

    async def search_groups(self, query: str, max_results: int = 40) -> List[Dict]:
        """Recherche des groupes Telegram sur plusieurs sources"""
        if not self.session:
            await self.init_session()

        all_groups = []
        search_tasks = []

        # Lancer des recherches parallèles sur toutes les sources
        for source in self.search_sources:
            task = self._search_single_source(source, query)
            search_tasks.append(task)

        # Attendre tous les résultats
        results = await asyncio.gather(*search_tasks, return_exceptions=True)
        
        # Compiler tous les résultats
        for result in results:
            if isinstance(result, list):
                all_groups.extend(result)

        # Supprimer les doublons et limiter les résultats
        unique_groups = self._remove_duplicates(all_groups)
        
        # Trier par nombre de membres (décroissant)
        unique_groups.sort(key=lambda x: x.get('members', 0), reverse=True)
        
        # Assurer un minimum de 3 résultats et un maximum de max_results
        final_groups = unique_groups[:max_results]
        
        if len(final_groups) < 3:
            # Si pas assez de résultats, essayer une recherche plus large
            broader_results = await self._broader_search(query)
            final_groups.extend(broader_results)
            final_groups = self._remove_duplicates(final_groups)[:max_results]

        return final_groups

    async def _search_single_source(self, source: Dict, query: str) -> List[Dict]:
        """Recherche sur une source spécifique"""
        try:
            search_url = source['base_url'] + source['search_pattern'].format(query=quote_plus(query))
            
            headers = {
                'User-Agent': random.choice(self.user_agents),
                'Referer': source['base_url']
            }
            
            async with self.session.get(search_url, headers=headers) as response:
                if response.status == 200:
                    content = await response.text()
                    return await source['parser'](content, query)
                else:
                    logger.warning(f"Erreur {response.status} pour {source['name']}")
                    return []
                    
        except Exception as e:
            logger.error(f"Erreur lors de la recherche sur {source['name']}: {e}")
            return []

    async def _parse_tlgrm(self, html_content: str, query: str) -> List[Dict]:
        """Parse les résultats de TLGRM.eu"""
        groups = []
        soup = BeautifulSoup(html_content, 'html.parser')
        
        for item in soup.find_all(['div', 'a'], class_=re.compile(r'channel|group|item')):
            try:
                # Extraire le lien
                link_elem = item.find('a', href=re.compile(r't\.me|telegram\.me'))
                if not link_elem:
                    link_elem = item if item.name == 'a' and item.get('href') else None
                
                if link_elem and link_elem.get('href'):
                    link = link_elem.get('href')
                    if not link.startswith('http'):
                        link = 'https://t.me/' + link.split('/')[-1]
                    
                    # Extraire le titre
                    title_elem = item.find(['h3', 'h4', 'span', 'div'], class_=re.compile(r'title|name'))
                    title = title_elem.get_text().strip() if title_elem else link.split('/')[-1]
                    
                    # Extraire la description
                    desc_elem = item.find(['p', 'div', 'span'], class_=re.compile(r'desc|about'))
                    description = desc_elem.get_text().strip() if desc_elem else ""
                    
                    # Extraire le nombre de membres
                    members_elem = item.find(text=re.compile(r'\d+\s*(members?|subscribers?)', re.I))
                    members = self._extract_number(members_elem) if members_elem else 0
                    
                    groups.append({
                        'title': title,
                        'link': link,
                        'description': description[:100] + "..." if len(description) > 100 else description,
                        'members': members,
                        'source': 'TLGRM.eu'
                    })
                    
            except Exception as e:
                logger.debug(f"Erreur parsing TLGRM item: {e}")
                continue
                
        return groups

    async def _parse_telegram_channels(self, html_content: str, query: str) -> List[Dict]:
        """Parse les résultats de TelegramChannels.me"""
        groups = []
        soup = BeautifulSoup(html_content, 'html.parser')
        
        for item in soup.find_all(['div', 'li'], class_=re.compile(r'channel|group|result')):
            try:
                link_elem = item.find('a', href=re.compile(r't\.me|telegram\.me'))
                if link_elem and link_elem.get('href'):
                    link = link_elem.get('href')
                    
                    title = link_elem.get_text().strip() or link.split('/')[-1]
                    
                    desc_elem = item.find(['p', 'div'], class_=re.compile(r'desc|summary'))
                    description = desc_elem.get_text().strip() if desc_elem else ""
                    
                    members_text = item.find(text=re.compile(r'\d+\s*(members?|subs)', re.I))
                    members = self._extract_number(members_text) if members_text else 0
                    
                    groups.append({
                        'title': title,
                        'link': link,
                        'description': description[:100] + "..." if len(description) > 100 else description,
                        'members': members,
                        'source': 'TelegramChannels.me'
                    })
                    
            except Exception as e:
                logger.debug(f"Erreur parsing TelegramChannels item: {e}")
                continue
                
        return groups

    async def _parse_telegram_store(self, html_content: str, query: str) -> List[Dict]:
        """Parse les résultats de Telegram-Store"""
        groups = []
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Rechercher tous les liens Telegram
        telegram_links = soup.find_all('a', href=re.compile(r't\.me|telegram\.me'))
        
        for link_elem in telegram_links:
            try:
                link = link_elem.get('href')
                if link and ('t.me' in link or 'telegram.me' in link):
                    # Nettoyer le lien
                    if not link.startswith('http'):
                        link = 'https://' + link
                    
                    # Trouver le conteneur parent pour plus d'infos
                    parent = link_elem.find_parent(['div', 'li', 'article'])
                    
                    title = link_elem.get_text().strip()
                    if not title or len(title) < 2:
                        title = link.split('/')[-1].replace('@', '')
                    
                    description = ""
                    if parent:
                        desc_elem = parent.find(['p', 'span', 'div'], class_=re.compile(r'desc|about|summary'))
                        if desc_elem:
                            description = desc_elem.get_text().strip()
                    
                    groups.append({
                        'title': title,
                        'link': link,
                        'description': description[:100] + "..." if len(description) > 100 else description,
                        'members': 0,
                        'source': 'Telegram-Store'
                    })
                    
            except Exception as e:
                logger.debug(f"Erreur parsing Telegram-Store item: {e}")
                continue
                
        return groups

    async def _broader_search(self, query: str) -> List[Dict]:
        """Recherche plus large si pas assez de résultats"""
        broader_terms = [
            query.split()[0] if ' ' in query else query,  # Premier mot seulement
            f"{query} group",
            f"{query} channel",
            f"telegram {query}"
        ]
        
        additional_groups = []
        for term in broader_terms:
            if term != query:  # Éviter de rechercher le même terme
                for source in self.search_sources[:2]:  # Limiter aux 2 premières sources
                    try:
                        results = await self._search_single_source(source, term)
                        additional_groups.extend(results)
                        if len(additional_groups) >= 10:  # Arrêter si on a assez de résultats
                            break
                    except:
                        continue
                if len(additional_groups) >= 10:
                    break
                    
        return additional_groups

    def _remove_duplicates(self, groups: List[Dict]) -> List[Dict]:
        """Supprime les doublons basés sur les liens"""
        seen_links = set()
        unique_groups = []
        
        for group in groups:
            link = group['link'].lower().strip('/')
            # Normaliser les liens t.me
            if 't.me/' in link:
                link = link.split('t.me/')[-1].split('?')[0]
            
            if link not in seen_links:
                seen_links.add(link)
                unique_groups.append(group)
                
        return unique_groups

    def _extract_number(self, text: str) -> int:
        """Extrait un nombre d'un texte"""
        if not text:
            return 0
        numbers = re.findall(r'\d+', str(text))
        return int(numbers[0]) if numbers else 0

# Bot Telegram
class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.searcher = TelegramGroupSearcher()
        self.application = Application.builder().token(token).build()
        self.setup_handlers()

    def setup_handlers(self):
        """Configure les gestionnaires de commandes"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("search", self.search_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /start"""
        welcome_text = """
🤖 **Bot de Recherche de Groupes Telegram**

Bienvenue ! Je peux vous aider à trouver des groupes Telegram publics.

**Commandes disponibles :**
• `/search <mot-clé>` - Rechercher des groupes
• `/help` - Afficher l'aide

**Exemples :**
• `/search musique`
• `/search technologie`
• `/search crypto`

Tapez simplement votre recherche pour commencer ! 🔍
        """
        
        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.MARKDOWN
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /help"""
        help_text = """
📖 **Guide d'utilisation**

**Comment rechercher :**
1. `/search <votre recherche>` ou tapez directement votre recherche
2. Le bot trouvera entre 3 et 40 groupes correspondants
3. Cliquez sur les liens pour rejoindre les groupes

**Conseils pour de meilleurs résultats :**
• Utilisez des mots-clés simples
• Essayez en français et en anglais
• Soyez spécifique dans vos recherches

**Exemples de recherches populaires :**
• Crypto, Bitcoin, Trading
• Musique, Films, Séries
• Programmation, Tech, IA
• Sport, Football, Gaming

Le bot recherche sur plusieurs sources pour vous garantir les meilleurs résultats ! 🚀
        """
        
        await update.message.reply_text(
            help_text,
            parse_mode=ParseMode.MARKDOWN
        )

    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /search"""
        if not context.args:
            await update.message.reply_text(
                "❌ Veuillez spécifier votre recherche.\n\n"
                "Exemple: `/search musique`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        query = ' '.join(context.args)
        await self.perform_search(update, query)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère les messages texte comme des recherches"""
        query = update.message.text.strip()
        if len(query) > 1:
            await self.perform_search(update, query)

    async def perform_search(self, update: Update, query: str):
        """Effectue la recherche et envoie les résultats"""
        # Message de recherche en cours
        searching_msg = await update.message.reply_text(
            f"🔍 Recherche en cours pour '*{query}*'...\n"
            "Cela peut prendre quelques secondes.",
            parse_mode=ParseMode.MARKDOWN
        )

        try:
            # Effectuer la recherche
            start_time = time.time()
            groups = await self.searcher.search_groups(query, max_results=40)
            search_time = time.time() - start_time

            # Supprimer le message de recherche
            await searching_msg.delete()

            if not groups:
                await update.message.reply_text(
                    f"❌ Aucun groupe trouvé pour '*{query}*'.\n\n"
                    "💡 Essayez avec:\n"
                    "• Des mots-clés différents\n"
                    "• Des termes plus généraux\n"
                    "• Des mots en anglais",
                    parse_mode=ParseMode.MARKDOWN
                )
                return

            # Limiter entre 3 et 40 résultats
            if len(groups) < 3:
                # Ajouter des résultats génériques si nécessaire
                pass
            elif len(groups) > 40:
                groups = groups[:40]

            # Envoyer les résultats par paquets
            await self.send_results(update, query, groups, search_time)

        except Exception as e:
            logger.error(f"Erreur lors de la recherche: {e}")
            await searching_msg.delete()
            await update.message.reply_text(
                "❌ Une erreur s'est produite lors de la recherche.\n"
                "Veuillez réessayer dans quelques instants."
            )

    async def send_results(self, update: Update, query: str, groups: List[Dict], search_time: float):
        """Envoie les résultats de recherche"""
        total_groups = len(groups)
        
        # Message d'en-tête
        header = (
            f"🎯 **Résultats pour '{query}'**\n"
            f"📊 {total_groups} groupes trouvés en {search_time:.1f}s\n\n"
        )

        # Diviser les résultats en paquets de 10
        chunks = [groups[i:i + 10] for i in range(0, len(groups), 10)]
        
        for i, chunk in enumerate(chunks):
            message_text = header if i == 0 else ""
            
            for j, group in enumerate(chunk, start=i*10 + 1):
                # Icône basée sur la source
                icon = "🔷" if "TLGRM" in group.get('source', '') else "🔹"
                
                # Informations sur les membres
                members_info = f" • {group['members']} membres" if group['members'] > 0 else ""
                
                # Formatage du groupe
                title = group['title'][:50] + "..." if len(group['title']) > 50 else group['title']
                
                message_text += (
                    f"{icon} **{j}. {title}**{members_info}\n"
                    f"🔗 {group['link']}\n"
                )
                
                if group['description']:
                    desc = group['description'][:80] + "..." if len(group['description']) > 80 else group['description']
                    message_text += f"📝 {desc}\n"
                
                message_text += "\n"

            # Ajouter des boutons pour la navigation si nécessaire
            keyboard = None
            if len(chunks) > 1 and i == len(chunks) - 1:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Nouvelle recherche", callback_data="new_search")]
                ])

            await update.message.reply_text(
                message_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
                disable_web_page_preview=True
            )

            # Délai entre les messages pour éviter le spam
            if i < len(chunks) - 1:
                await asyncio.sleep(1)

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère les callbacks des boutons"""
        query = update.callback_query
        await query.answer()

        if query.data == "new_search":
            await query.message.reply_text(
                "🔍 Tapez votre nouvelle recherche ou utilisez `/search <mot-clé>`"
            )

    async def run_webhook(self, webhook_url: str, port: int = 8000):
        """Lance le bot en mode webhook pour Render"""
        await self.searcher.init_session()
        
        await self.application.initialize()
        await self.application.start()
        
        # Configurer le webhook
        await self.application.bot.set_webhook(
            url=f"{webhook_url}/webhook",
            allowed_updates=["message", "callback_query"]
        )
        
        # Lancer le serveur webhook
        await self.application.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=f"{webhook_url}/webhook"
        )

    async def run_polling(self):
        """Lance le bot en mode polling pour les tests locaux"""
        await self.searcher.init_session()
        await self.application.run_polling()

# Point d'entrée principal
async def main():
    # Token du bot depuis les variables d'environnement
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN non défini dans les variables d'environnement")
        return

    bot = TelegramBot(bot_token)
    
    # Mode selon l'environnement
    if os.getenv('RENDER'):  # Sur Render
        webhook_url = os.getenv('WEBHOOK_URL')
        port = int(os.getenv('PORT', 8000))
        await bot.run_webhook(webhook_url, port)
    else:  # En local
        await bot.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
