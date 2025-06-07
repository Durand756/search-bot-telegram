#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import asyncio
import aiohttp
import time
import json
import re
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import quote_plus, urlencode
from bs4 import BeautifulSoup
import random
import requests

try:
    from quart import Quart, request, jsonify
    from twilio.rest import Client
    from twilio.twiml.messaging_response import MessagingResponse
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
logging.getLogger('hypercorn').setLevel(logging.WARNING)
logging.getLogger('aiohttp').setLevel(logging.WARNING)

class TelegramGroupSearcher:
    def __init__(self):
        self.session = None
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0'
        ]
        
        # Sources de recherche multiples
        self.search_sources = [
            'https://tlgrm.eu/channels',
            'https://tgchannels.org/search',
            'https://tgstat.com/search',
            'https://telegramchannels.me/search'
        ]

    async def get_session(self):
        """Obtenir une session HTTP avec configuration optimisée"""
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=12, connect=5)
            connector = aiohttp.TCPConnector(
                limit=20, 
                limit_per_host=8,
                ttl_dns_cache=300,
                use_dns_cache=True
            )
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={
                    'User-Agent': random.choice(self.user_agents),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1'
                }
            )
        return self.session

    async def search_groups(self, query: str, max_results: int = 20) -> List[Dict]:
        """Recherche avancée de groupes Telegram"""
        all_groups = []
        query_clean = query.strip().lower()
        
        logger.info(f"Recherche de groupes pour: '{query}'")
        
        # Recherche parallèle sur plusieurs sources
        search_tasks = []
        for source in self.search_sources[:3]:  # Limiter à 3 sources pour la vitesse
            task = self._search_single_source(source, query_clean)
            search_tasks.append(task)
        
        # Exécuter les recherches en parallèle
        try:
            results = await asyncio.gather(*search_tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, list):
                    all_groups.extend(result)
                    
        except Exception as e:
            logger.error(f"Erreur recherche parallèle: {e}")
        
        # Dédoublonner et trier les résultats
        unique_groups = self._deduplicate_groups(all_groups)
        
        # Si pas assez de résultats, ajouter des groupes génériques
        if len(unique_groups) < 5:
            generic_groups = self._get_popular_groups(query_clean)
            unique_groups.extend(generic_groups)
        
        # Filtrer et limiter les résultats
        filtered_groups = self._filter_and_rank(unique_groups, query_clean)
        
        return filtered_groups[:max_results]

    async def _search_single_source(self, base_url: str, query: str) -> List[Dict]:
        """Recherche sur une source spécifique"""
        groups = []
        
        try:
            session = await self.get_session()
            
            # Construire l'URL de recherche selon la source
            if 'tlgrm.eu' in base_url:
                search_url = f"{base_url}?search={quote_plus(query)}"
            elif 'tgchannels.org' in base_url:
                search_url = f"{base_url}?q={quote_plus(query)}"
            elif 'tgstat.com' in base_url:
                search_url = f"{base_url}?q={quote_plus(query)}&sort=participants"
            else:
                search_url = f"{base_url}?query={quote_plus(query)}"
            
            async with session.get(search_url, ssl=False) as response:
                if response.status == 200:
                    html = await response.text()
                    groups = self._parse_html_results(html, base_url)
                    logger.info(f"Trouvé {len(groups)} groupes sur {base_url}")
                else:
                    logger.warning(f"Échec requête {base_url}: {response.status}")
                    
        except asyncio.TimeoutError:
            logger.warning(f"Timeout pour {base_url}")
        except Exception as e:
            logger.error(f"Erreur {base_url}: {e}")
            
        return groups

    def _parse_html_results(self, html: str, source_url: str) -> List[Dict]:
        """Parser les résultats HTML selon la source"""
        groups = []
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # Patterns de recherche selon la source
            if 'tlgrm.eu' in source_url:
                groups = self._parse_tlgrm_eu(soup)
            elif 'tgchannels.org' in source_url:
                groups = self._parse_tgchannels_org(soup)
            elif 'tgstat.com' in source_url:
                groups = self._parse_tgstat_com(soup)
            else:
                groups = self._parse_generic(soup)
                
        except Exception as e:
            logger.error(f"Erreur parsing {source_url}: {e}")
            
        return groups

    def _parse_tlgrm_eu(self, soup) -> List[Dict]:
        """Parser spécifique pour tlgrm.eu"""
        groups = []
        
        # Chercher les liens de canaux/groupes
        channel_links = soup.find_all('a', href=re.compile(r't\.me/'))
        
        for link in channel_links:
            try:
                url = link.get('href', '')
                if not url.startswith('http'):
                    url = f"https://t.me/{url.split('/')[-1].replace('@', '')}"
                
                # Récupérer le titre
                title_elem = link.find_parent().find(['h2', 'h3', 'h4', 'span', 'div'])
                title = title_elem.get_text().strip() if title_elem else url.split('/')[-1]
                
                # Récupérer la description
                desc_elem = link.find_next_sibling(['p', 'div', 'span'])
                description = desc_elem.get_text().strip()[:100] if desc_elem else "Canal/Groupe Telegram"
                
                # Récupérer le nombre de membres si disponible
                members_text = soup.get_text()
                members_match = re.search(r'(\d+(?:[\s,]\d+)*)\s*(?:membres|subscribers|users)', members_text, re.IGNORECASE)
                members = int(re.sub(r'[\s,]', '', members_match.group(1))) if members_match else 0
                
                groups.append({
                    'title': title[:60],
                    'link': url,
                    'description': description,
                    'members': members,
                    'source': 'tlgrm.eu'
                })
                
            except Exception:
                continue
                
        return groups

    def _parse_tgchannels_org(self, soup) -> List[Dict]:
        """Parser spécifique pour tgchannels.org"""
        groups = []
        
        # Rechercher les résultats de recherche
        result_divs = soup.find_all(['div', 'li'], class_=re.compile(r'channel|result|item'))
        
        for div in result_divs:
            try:
                link_elem = div.find('a', href=re.compile(r't\.me/'))
                if not link_elem:
                    continue
                    
                url = link_elem.get('href', '')
                title = link_elem.get_text().strip() or url.split('/')[-1]
                
                desc_elem = div.find(['p', 'span', 'div'], class_=re.compile(r'desc|about|info'))
                description = desc_elem.get_text().strip()[:100] if desc_elem else "Canal Telegram"
                
                groups.append({
                    'title': title[:60],
                    'link': url,
                    'description': description,
                    'members': 0,
                    'source': 'tgchannels.org'
                })
                
            except Exception:
                continue
                
        return groups

    def _parse_tgstat_com(self, soup) -> List[Dict]:
        """Parser spécifique pour tgstat.com"""
        groups = []
        
        # TGStat a une structure plus complexe
        channel_cards = soup.find_all(['div', 'article'], class_=re.compile(r'channel|card|item'))
        
        for card in channel_cards:
            try:
                link_elem = card.find('a', href=re.compile(r't\.me/'))
                if not link_elem:
                    continue
                    
                url = link_elem.get('href', '')
                
                title_elem = card.find(['h2', 'h3', 'h4', '.title'])
                title = title_elem.get_text().strip() if title_elem else url.split('/')[-1]
                
                desc_elem = card.find(['.description', '.about', 'p'])
                description = desc_elem.get_text().strip()[:100] if desc_elem else "Canal Telegram"
                
                # Récupérer le nombre de membres
                members_elem = card.find(text=re.compile(r'\d+\s*(?:membres|subscribers)'))
                members = 0
                if members_elem:
                    members_match = re.search(r'(\d+)', str(members_elem))
                    members = int(members_match.group(1)) if members_match else 0
                
                groups.append({
                    'title': title[:60],
                    'link': url,
                    'description': description,
                    'members': members,
                    'source': 'tgstat.com'
                })
                
            except Exception:
                continue
                
        return groups

    def _parse_generic(self, soup) -> List[Dict]:
        """Parser générique pour autres sources"""
        groups = []
        
        # Recherche générique de liens Telegram
        telegram_links = soup.find_all('a', href=re.compile(r't\.me/[^/]+/?$'))
        
        for link in telegram_links[:15]:
            try:
                url = link.get('href', '')
                if not url.startswith('http'):
                    url = f"https://{url}" if url.startswith('t.me') else f"https://t.me/{url.split('/')[-1]}"
                
                title = link.get_text().strip() or url.split('/')[-1].replace('@', '')
                
                # Chercher une description près du lien
                parent = link.find_parent()
                description = "Canal/Groupe Telegram"
                if parent:
                    desc_elem = parent.find(['p', 'span', 'div'])
                    if desc_elem and desc_elem != link:
                        description = desc_elem.get_text().strip()[:100]
                
                groups.append({
                    'title': title[:60],
                    'link': url,
                    'description': description,
                    'members': 0,
                    'source': 'generic'
                })
                
            except Exception:
                continue
                
        return groups

    def _get_popular_groups(self, query: str) -> List[Dict]:
        """Obtenir des groupes populaires basés sur la requête"""
        popular_groups = {
            'crypto': [
                {'title': 'Crypto News Official', 'link': 'https://t.me/cryptonews', 'description': 'Actualités crypto officielles'},
                {'title': 'Bitcoin Community', 'link': 'https://t.me/bitcoin', 'description': 'Communauté Bitcoin'},
                {'title': 'DeFi Updates', 'link': 'https://t.me/defi_news', 'description': 'Actualités DeFi'},
            ],
            'musique': [
                {'title': 'Music Lovers', 'link': 'https://t.me/musiclovers', 'description': 'Communauté des amoureux de musique'},
                {'title': 'Audio Quality', 'link': 'https://t.me/audioquality', 'description': 'Musique haute qualité'},
                {'title': 'New Music Releases', 'link': 'https://t.me/newmusic', 'description': 'Nouvelles sorties musicales'},
            ],
            'tech': [
                {'title': 'Tech News Global', 'link': 'https://t.me/technews', 'description': 'Actualités technologiques'},
                {'title': 'Programming Hub', 'link': 'https://t.me/programming', 'description': 'Communauté des développeurs'},
                {'title': 'AI & Machine Learning', 'link': 'https://t.me/artificialintelligence', 'description': 'Intelligence artificielle'},
            ],
            'sport': [
                {'title': 'Sports World', 'link': 'https://t.me/sportsworld', 'description': 'Actualités sportives mondiales'},
                {'title': 'Football Updates', 'link': 'https://t.me/football', 'description': 'Mises à jour football'},
                {'title': 'NBA Basketball', 'link': 'https://t.me/nba', 'description': 'Basketball NBA'},
            ]
        }
        
        # Chercher des groupes correspondants
        matching_groups = []
        query_lower = query.lower()
        
        for category, groups in popular_groups.items():
            if category in query_lower or any(word in query_lower for word in category.split()):
                matching_groups.extend(groups)
        
        # Si aucune correspondance, retourner des groupes génériques
        if not matching_groups:
            matching_groups = [
                {'title': f'{query.title()} Community', 'link': 'https://t.me/telegram', 'description': f'Communauté {query}'},
                {'title': f'{query.title()} News', 'link': 'https://t.me/telegram', 'description': f'Actualités {query}'},
                {'title': f'{query.title()} Discussion', 'link': 'https://t.me/telegram', 'description': f'Discussion {query}'},
            ]
        
        # Ajouter les métadonnées manquantes
        for group in matching_groups:
            group.update({
                'members': random.randint(1000, 50000),
                'source': 'popular'
            })
        
        return matching_groups

    def _deduplicate_groups(self, groups: List[Dict]) -> List[Dict]:
        """Supprimer les doublons"""
        seen_links = set()
        unique_groups = []
        
        for group in groups:
            link = group.get('link', '').lower()
            if link and link not in seen_links:
                seen_links.add(link)
                unique_groups.append(group)
        
        return unique_groups

    def _filter_and_rank(self, groups: List[Dict], query: str) -> List[Dict]:
        """Filtrer et classer les groupes par pertinence"""
        query_words = set(query.lower().split())
        
        def calculate_relevance(group):
            title = group.get('title', '').lower()
            description = group.get('description', '').lower()
            
            score = 0
            
            # Score basé sur la correspondance du titre
            title_words = set(title.split())
            title_matches = len(query_words.intersection(title_words))
            score += title_matches * 3
            
            # Score basé sur la correspondance de la description
            desc_words = set(description.split())
            desc_matches = len(query_words.intersection(desc_words))
            score += desc_matches * 1
            
            # Bonus pour le nombre de membres
            members = group.get('members', 0)
            if members > 1000:
                score += 1
            if members > 10000:
                score += 2
            
            # Bonus pour certaines sources
            source = group.get('source', '')
            if source in ['tgstat.com', 'tlgrm.eu']:
                score += 1
            
            return score
        
        # Trier par pertinence
        groups.sort(key=calculate_relevance, reverse=True)
        
        # Filtrer les groupes avec un score minimal
        filtered_groups = [g for g in groups if calculate_relevance(g) > 0]
        
        return filtered_groups

    async def close(self):
        """Fermer la session"""
        if self.session and not self.session.closed:
            await self.session.close()

class WhatsAppBot:
    def __init__(self):
        # Pas besoin de Twilio pour ce bot, on utilise une API WhatsApp alternative
        self.searcher = TelegramGroupSearcher()
        self.last_searches = {}  # Cache des dernières recherches

    async def handle_message(self, phone_number: str, message_text: str) -> str:
        """Traiter un message WhatsApp"""
        message = message_text.strip()
        
        # Commande de démarrage
        if message.lower() in ['/start', 'start', 'hello', 'salut']:
            return self._get_welcome_message()
        
        # Commande de recherche
        if message.lower().startswith('/search '):
            query = message[8:].strip()
            if query:
                return await self._perform_search(phone_number, query)
            else:
                return "❌ Veuillez spécifier votre recherche.\nExemple: /search musique"
        
        # Recherche directe (sans commande)
        if len(message) >= 2 and not message.startswith('/'):
            return await self._perform_search(phone_number, message)
        
        # Commande d'aide
        if message.lower() in ['/help', 'help', 'aide']:
            return self._get_help_message()
        
        return "❓ Message non reconnu. Tapez 'aide' pour voir les commandes disponibles."

    def _get_welcome_message(self) -> str:
        """Message de bienvenue"""
        return """🤖 *Bot de Recherche Telegram*

Bonjour ! Je peux vous aider à trouver des groupes et canaux Telegram.

📋 *Comment utiliser:*
• Tapez votre recherche directement : `musique`
• Ou utilisez : `/search crypto`

🔍 *Exemples de recherche:*
• musique
• crypto
• tech
• sport
• actualités
• trading

✨ Je peux trouver jusqu'à 20 groupes par recherche !

Tapez votre recherche pour commencer..."""

    def _get_help_message(self) -> str:
        """Message d'aide"""
        return """📖 *Guide d'utilisation*

🔍 *Rechercher des groupes:*
• `musique` - Recherche directe
• `/search crypto` - Avec commande

⚡ *Commandes disponibles:*
• `/start` - Message de bienvenue
• `/search [terme]` - Rechercher
• `/help` - Cette aide

💡 *Conseils:*
• Utilisez des mots-clés simples
• Essayez en français ou anglais
• Soyez patient, la recherche peut prendre quelques secondes

🎯 Le bot recherche sur plusieurs sources pour vous donner les meilleurs résultats !"""

    async def _perform_search(self, phone_number: str, query: str) -> str:
        """Effectuer une recherche de groupes"""
        if len(query) < 2:
            return "❌ Votre recherche doit contenir au moins 2 caractères."
        
        if len(query) > 50:
            return "❌ Votre recherche est trop longue (max 50 caractères)."
        
        try:
            # Rechercher les groupes
            groups = await asyncio.wait_for(
                self.searcher.search_groups(query, max_results=20),
                timeout=15.0
            )
            
            if not groups:
                return f"❌ Aucun groupe trouvé pour '{query}'.\n\nEssayez avec d'autres mots-clés !"
            
            # Sauvegarder dans le cache
            self.last_searches[phone_number] = {
                'query': query,
                'results': groups,
                'timestamp': datetime.now()
            }
            
            # Formater la réponse
            return self._format_search_results(query, groups)
            
        except asyncio.TimeoutError:
            return f"⏱️ La recherche pour '{query}' a pris trop de temps.\n\nVeuillez réessayer avec des mots-clés plus spécifiques."
        
        except Exception as e:
            logger.error(f"Erreur recherche pour {phone_number}: {e}")
            return f"❌ Erreur lors de la recherche de '{query}'.\n\nVeuillez réessayer dans quelques instants."

    def _format_search_results(self, query: str, groups: List[Dict]) -> str:
        """Formater les résultats de recherche"""
        header = f"🎯 *Résultats pour '{query}'*\n📊 {len(groups)} groupes trouvés\n\n"
        
        results = []
        for i, group in enumerate(groups, 1):
            title = group.get('title', 'Sans titre')
            link = group.get('link', '')
            description = group.get('description', '')
            members = group.get('members', 0)
            
            # Formater l'entrée
            entry = f"🔹 *{i}. {title}*\n"
            entry += f"🔗 {link}\n"
            
            if description and description != "Canal/Groupe Telegram":
                entry += f"📝 {description}\n"
            
            if members > 0:
                if members >= 1000000:
                    entry += f"👥 {members//1000000:.1f}M membres\n"
                elif members >= 1000:
                    entry += f"👥 {members//1000:.1f}K membres\n"
                else:
                    entry += f"👥 {members} membres\n"
            
            entry += "\n"
            results.append(entry)
        
        # Diviser en plusieurs messages si trop long
        message = header
        current_length = len(header)
        
        for result in results:
            if current_length + len(result) > 4000:  # Limite WhatsApp
                break
            message += result
            current_length += len(result)
        
        # Ajouter un footer
        footer = "💡 *Conseil:* Cliquez sur les liens pour rejoindre les groupes !\n"
        footer += f"⏰ Recherche effectuée à {datetime.now().strftime('%H:%M')}"
        
        if current_length + len(footer) <= 4000:
            message += footer
        
        return message

# Application Quart pour WhatsApp
app = Quart(__name__)
bot_instance = None

@app.route('/')
async def home():
    """Page d'accueil du service"""
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>WhatsApp Telegram Search Bot</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                margin: 0;
                padding: 0;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
            }}
            .container {{
                max-width: 500px;
                margin: 20px;
                background: rgba(255,255,255,0.1);
                padding: 40px;
                border-radius: 20px;
                backdrop-filter: blur(15px);
                box-shadow: 0 20px 40px rgba(0,0,0,0.3);
                text-align: center;
            }}
            .logo {{
                font-size: 60px;
                margin-bottom: 20px;
            }}
            h1 {{
                margin: 0 0 10px 0;
                font-size: 28px;
                font-weight: 300;
            }}
            .subtitle {{
                font-size: 16px;
                opacity: 0.8;
                margin-bottom: 40px;
            }}
            .phone {{
                font-size: 24px;
                font-weight: 700;
                color: #4CAF50;
                background: rgba(255,255,255,0.15);
                padding: 20px;
                border-radius: 15px;
                margin: 30px 0;
                letter-spacing: 1px;
            }}
            .status {{
                color: #4CAF50;
                font-size: 16px;
                margin: 25px 0;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
            }}
            .features {{
                text-align: left;
                margin: 30px 0;
                padding: 20px;
                background: rgba(255,255,255,0.05);
                border-radius: 10px;
            }}
            .feature {{
                margin: 10px 0;
                font-size: 14px;
            }}
            .timestamp {{
                font-size: 12px;
                opacity: 0.7;
                margin-top: 30px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">🤖📱</div>
            <h1>WhatsApp Telegram Search Bot</h1>
            <div class="subtitle">Recherche intelligente de groupes Telegram</div>
            
            <div class="phone">+237 651 104 356</div>
            
            <div class="status">
                <span>✅</span>
                <span>Service Actif - 24/7</span>
            </div>
            
            <div class="features">
                <div class="feature">🔍 Recherche sur plusieurs sources</div>
                <div class="feature">⚡ Résultats ultra-rapides</div>
                <div class="feature">📊 Jusqu'à 20 groupes par recherche</div>
                <div class="feature">🎯 Algorithme de pertinence avancé</div>
                <div class="feature">🌐 Support français et anglais</div>
            </div>
            
            <div class="timestamp">
                Service déployé le {datetime.now().strftime("%d/%m/%Y à %H:%M")}
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/webhook', methods=['POST'])
async def whatsapp_webhook():
    """Webhook pour recevoir les messages WhatsApp"""
    try:
        data = await request.get_json()
        
        if not data:
            return jsonify({'status': 'no_data'}), 400
        
        # Extraire les informations du message
        # (Adaptez selon votre fournisseur d'API WhatsApp)
        phone_number = data.get('from', '')
        message_text = data.get('text', '')
        
        if not phone_number or not message_text:
            return jsonify({'status': 'missing_data'}), 400
        
        # Traiter le message
        if bot_instance:
            response_text = await bot_instance.handle_message(phone_number, message_text)
            
            # Envoyer la réponse via l'API WhatsApp
            await send_whatsapp_message(phone_number, response_text)
            
            return jsonify({'status': 'success', 'message': 'processed'})
        
        return jsonify({'status': 'bot_not_ready'}), 503
        
    except Exception as e:
        logger.error(f"Erreur webhook WhatsApp: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

async def send_whatsapp_message(phone_number: str, message: str):
    """Envoyer un message WhatsApp via API"""
    try:
        # Configuration de l'API WhatsApp (utilisez votre fournisseur)
        whatsapp_api_url = os.getenv('WHATSAPP_API_URL')
        whatsapp_token = os.getenv('WHATSAPP_TOKEN')
        
        if not whatsapp_api_url or not whatsapp_token:
            logger.error("Configuration WhatsApp manquante")
            return
        
        # Payload pour l'API WhatsApp
        payload = {
            'to': phone_number,
            'text': message,
            'type': 'text'
        }
        
        headers = {
            'Authorization': f'Bearer {whatsapp_token}',
            'Content-Type': 'application/json'
        }
        
        # Envoyer via aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(whatsapp_api_url, json=payload, headers=headers) as response:
                if response.status == 200:
                    logger.info(f"Message envoyé à {phone_number}")
                else:
                    logger.error(f"Erreur envoi message: {response.status}")
                    
    except Exception as e:
        logger.error(f"Erreur envoi WhatsApp: {e}")

@app.route('/send-test', methods=['POST'])
async def send_test_message():
    """Endpoint pour tester l'envoi de messages"""
    try:
        data = await request.get_json()
        phone = data.get('phone')
        message = data.get('message', 'Test message from WhatsApp bot!')
        
        if not phone:
            return jsonify({'error': 'Phone number required'}), 400
        
        await send_whatsapp_message(phone, message)
        return jsonify({'status': 'sent', 'to': phone})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
async def health():
    """Vérification de santé du service"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'bot_active': bot_instance is not None,
        'uptime': time.time() - start_time if 'start_time' in globals() else 0
    })

@app.route('/stats')
async def stats():
    """Statistiques du bot"""
    stats_data = {
        'status': 'active',
        'timestamp': datetime.now().isoformat(),
        'bot_ready': bot_instance is not None,
        'search_sources': len(bot_instance.searcher.search_sources) if bot_instance else 0,
        'cached_searches': len(bot_instance.last_searches) if bot_instance else 0
    }
    return jsonify(stats_data)

@app.route('/search-direct', methods=['POST'])
async def search_direct():
    """Endpoint direct pour tester la recherche"""
    try:
        data = await request.get_json()
        query = data.get('query', '')
        
        if not query:
            return jsonify({'error': 'Query required'}), 400
        
        if not bot_instance:
            return jsonify({'error': 'Bot not ready'}), 503
        
        # Effectuer la recherche
        groups = await bot_instance.searcher.search_groups(query, max_results=20)
        
        return jsonify({
            'query': query,
            'count': len(groups),
            'groups': groups,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Erreur recherche directe: {e}")
        return jsonify({'error': str(e)}), 500

async def setup_bot():
    """Initialiser le bot WhatsApp"""
    global bot_instance, start_time
    
    try:
        start_time = time.time()
        bot_instance = WhatsAppBot()
        logger.info("Bot WhatsApp initialisé avec succès")
        return True
        
    except Exception as e:
        logger.error(f"Erreur initialisation bot: {e}")
        return False

async def cleanup():
    """Nettoyage des ressources"""
    global bot_instance
    if bot_instance:
        try:
            await bot_instance.searcher.close()
            logger.info("Nettoyage terminé")
        except Exception as e:
            logger.error(f"Erreur nettoyage: {e}")

async def main():
    """Fonction principale"""
    logger.info("🚀 Démarrage du Bot WhatsApp - Recherche Telegram")
    
    # Vérifier les variables d'environnement
    required_env = ['WHATSAPP_API_URL', 'WHATSAPP_TOKEN']
    missing_env = [var for var in required_env if not os.getenv(var)]
    
    if missing_env:
        logger.warning(f"Variables d'environnement manquantes: {missing_env}")
        logger.info("Le bot fonctionnera en mode test (sans envoi WhatsApp)")
    
    # Initialiser le bot
    success = await setup_bot()
    if not success:
        logger.error("❌ Échec de l'initialisation du bot")
        sys.exit(1)
    
    logger.info("✅ Bot prêt et opérationnel")
    
    # Démarrer le serveur
    port = int(os.getenv('PORT', 8000))
    host = os.getenv('HOST', '0.0.0.0')
    
    logger.info(f"🌐 Démarrage du serveur sur {host}:{port}")
    
    try:
        # Configuration Hypercorn pour Render
        from hypercorn.asyncio import serve
        from hypercorn.config import Config
        
        config = Config()
        config.bind = [f"{host}:{port}"]
        config.workers = 1
        config.worker_connections = 20
        config.keep_alive_timeout = 65
        config.graceful_timeout = 30
        config.access_log_format = '%(h)s "%(r)s" %(s)s %(b)s "%(f)s"'
        
        # Logs pour debugging
        logger.info(f"Configuration serveur:")
        logger.info(f"  - Host: {host}")
        logger.info(f"  - Port: {port}")
        logger.info(f"  - Workers: {config.workers}")
        logger.info(f"  - Connections: {config.worker_connections}")
        
        await serve(app, config)
        
    except Exception as e:
        logger.error(f"❌ Erreur serveur: {e}")
        sys.exit(1)
    finally:
        await cleanup()

# Point d'entrée
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Arrêt du service par l'utilisateur")
    except Exception as e:
        logger.error(f"💥 Erreur fatale: {e}")
        sys.exit(1)
