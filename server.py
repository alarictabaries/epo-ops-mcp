#!/usr/bin/env python3
"""
Serveur MCP pour explorer l'API Open Patent Services (OPS) de l'EPO
Basé sur FastMCP 2.0 et les spécifications OPS API 3.2
"""

import asyncio
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

# Load environment variables from .env file
load_dotenv()

# Configuration
OPS_BASE_URL = "https://ops.epo.org/3.2/rest-services"
USER_AGENT = "ops-mcp-server/1.0"
DEFAULT_TIMEOUT = 30.0

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP("OPS EPO API Explorer")

class OPSError(Exception):
    """Exception personnalisée pour les erreurs de l'API OPS"""
    pass

class OPSClient:
    """Client pour interagir avec l'API OPS EPO"""

    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token
        self.client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/xml"
        }
        if access_token:
            self.headers["Authorization"] = f"Bearer {access_token}"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def get_auth_token(self, consumer_key: str, consumer_secret: str) -> str:
        """Obtenir un token d'accès OAuth2"""
        auth_url = "https://ops.epo.org/3.2/auth/accesstoken"

        try:
            response = await self.client.post(
                auth_url,
                auth=(consumer_key, consumer_secret),
                data={"grant_type": "client_credentials"},
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()
            token_data = response.json()
            return token_data["access_token"]
        except httpx.HTTPError as e:
            raise OPSError(f"Erreur d'authentification: {e}")

    async def make_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """Effectuer une requête vers l'API OPS"""
        url = f"{OPS_BASE_URL}{endpoint}"

        try:
            response = await self.client.get(
                url,
                headers=self.headers,
                params=params or {}
            )
            response.raise_for_status()

            # Parse XML response
            if response.headers.get("content-type", "").startswith("application/xml"):
                return self._parse_xml_response(response.text)
            else:
                return {"raw_content": response.text}

        except httpx.HTTPError as e:
            raise OPSError(f"Erreur de requête vers {url}: {e}")

    def _parse_xml_response(self, xml_content: str) -> Dict[str, Any]:
        """Parser la réponse XML en dictionnaire"""
        try:
            root = ET.fromstring(xml_content)
            result = self._xml_to_dict(root)
            # Ensure we always return a dictionary
            if isinstance(result, str):
                return {"text_content": result}
            elif result is None:
                return {"empty": True}
            else:
                return result
        except ET.ParseError:
            return {"raw_xml": xml_content}

    def _xml_to_dict(self, element: ET.Element) -> Union[Dict[str, Any], str, None]:
        """Convertir un élément XML en dictionnaire"""
        result = {}

        # Attributs
        if element.attrib:
            result["@attributes"] = element.attrib

        # Texte
        if element.text and element.text.strip():
            if len(element) == 0:
                return element.text.strip()
            result["#text"] = element.text.strip()

        # Éléments enfants
        children = {}
        for child in element:
            child_data = self._xml_to_dict(child)

            if child.tag in children:
                if not isinstance(children[child.tag], list):
                    children[child.tag] = [children[child.tag]]
                children[child.tag].append(child_data)
            else:
                children[child.tag] = child_data

        if children:
            result.update(children)

        return result if result else None

# Instance globale du client OPS
ops_client = None

@mcp.tool()
async def authenticate_ops_env() -> str:
    """
    Authentifier avec l'API OPS EPO en utilisant les clés du fichier .env.

    Returns:
        Message de confirmation de l'authentification
    """
    global ops_client

    # Get credentials from environment variables
    consumer_key = os.getenv("OPS_ID")
    consumer_secret = os.getenv("OPS_SECRET")

    if not consumer_key or not consumer_secret:
        return "Erreur: Variables d'environnement OPS_ID et OPS_SECRET non trouvées dans le fichier .env"

    try:
        ops_client = OPSClient()
        token = await ops_client.get_auth_token(consumer_key, consumer_secret)

        # Recréer le client avec le token
        await ops_client.__aexit__(None, None, None)
        ops_client = OPSClient(access_token=token)

        return f"Authentification réussie ! Token obtenu et configuré avec les clés du .env."

    except Exception as e:
        return f"Erreur d'authentification: {str(e)}"

@mcp.tool()
async def authenticate_ops(consumer_key: str, consumer_secret: str) -> str:
    """
    Authentifier avec l'API OPS EPO en utilisant les clés consumer OAuth2.

    Args:
        consumer_key: Clé consumer OAuth2 fournie par l'EPO
        consumer_secret: Secret consumer OAuth2 fourni par l'EPO

    Returns:
        Message de confirmation de l'authentification
    """
    global ops_client

    try:
        ops_client = OPSClient()
        token = await ops_client.get_auth_token(consumer_key, consumer_secret)

        # Recréer le client avec le token
        await ops_client.__aexit__(None, None, None)
        ops_client = OPSClient(access_token=token)

        return f"Authentification réussie ! Token obtenu et configuré."

    except Exception as e:
        return f"Erreur d'authentification: {str(e)}"


@mcp.tool()
async def search_patents(
    query: str,
    constituent: str = "biblio",
    range_param: str = "1-25"
) -> Dict[str, Any]:
    """
    Rechercher des brevets dans la base de données OPS.

    Args:
        query: Requête de recherche utilisant la syntaxe OPS. EXEMPLES PRÉCIS :
            - "ti=artificial intelligence" (titre)
            - "ab=solar panel" (abrégé)
            - "pa=Google" (déposant - PAS de guillemets)
            - "in=John Doe" (inventeur - PAS de guillemets)
            - "ic=A01B" (classification CPC)
            - "pd=20200101->20201231" (date publication)
            - Combinaisons : "ti=battery AND pa=Tesla"

            IMPORTANT : N'utilisez PAS de guillemets autour des valeurs !
            ❌ Incorrect : 'in="Alaric Tabaries"'
            ✅ Correct : 'in=Alaric Tabaries'

        constituent: Type de données à retourner ("biblio", "full-cycle", "abstract")
        range_param: Plage de résultats (ex: "1-25", "26-50")

    Returns:
        Résultats de la recherche sous forme de dictionnaire
    """
    if not ops_client:
        return {"error": "Veuillez d'abord vous authentifier avec authenticate_ops()"}

    try:
        endpoint = f"/published-data/search/{constituent}" if constituent != "biblio" else "/published-data/search"
        params = {"q": query, "Range": range_param}

        result = await ops_client.make_request(endpoint, params)
        return {
            "query": query,
            "constituent": constituent,
            "range": range_param,
            "results": result
        }

    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def get_patent_biblio(
    reference_type: str,
    reference_format: str,
    number: str
) -> Dict[str, Any]:
    """
    Récupérer les données bibliographiques d'un brevet.

    Args:
        reference_type: Type de référence ("publication", "application", "priority")
        reference_format: Format de référence ("docdb", "epodoc")
        number: Numéro du brevet (ex: "EP1000000", "US20050123456")

    Returns:
        Données bibliographiques du brevet
    """
    if not ops_client:
        return {"error": "Veuillez d'abord vous authentifier avec authenticate_ops()"}

    try:
        endpoint = f"/published-data/{reference_type}/{reference_format}/{number}/biblio"
        result = await ops_client.make_request(endpoint)

        return {
            "reference_type": reference_type,
            "reference_format": reference_format,
            "number": number,
            "biblio_data": result
        }

    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def get_patent_abstract(
    reference_type: str,
    reference_format: str,
    number: str
) -> Dict[str, Any]:
    """
    Récupérer l'abrégé d'un brevet.

    Args:
        reference_type: Type de référence ("publication", "application", "priority")
        reference_format: Format de référence ("docdb", "epodoc")
        number: Numéro du brevet

    Returns:
        Abrégé du brevet
    """
    if not ops_client:
        return {"error": "Veuillez d'abord vous authentifier avec authenticate_ops()"}

    try:
        endpoint = f"/published-data/{reference_type}/{reference_format}/{number}/abstract"
        result = await ops_client.make_request(endpoint)

        return {
            "reference_type": reference_type,
            "reference_format": reference_format,
            "number": number,
            "abstract_data": result
        }

    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def get_patent_claims(
    reference_type: str,
    reference_format: str,
    number: str
) -> Dict[str, Any]:
    """
    Récupérer les revendications d'un brevet.

    Args:
        reference_type: Type de référence ("publication", "application", "priority")
        reference_format: Format de référence ("docdb", "epodoc")
        number: Numéro du brevet

    Returns:
        Revendications du brevet
    """
    if not ops_client:
        return {"error": "Veuillez d'abord vous authentifier avec authenticate_ops()"}

    try:
        endpoint = f"/published-data/{reference_type}/{reference_format}/{number}/claims"
        result = await ops_client.make_request(endpoint)

        return {
            "reference_type": reference_type,
            "reference_format": reference_format,
            "number": number,
            "claims_data": result
        }

    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def get_patent_description(
    reference_type: str,
    reference_format: str,
    number: str
) -> Dict[str, Any]:
    """
    Récupérer la description d'un brevet.

    Args:
        reference_type: Type de référence ("publication", "application", "priority")
        reference_format: Format de référence ("docdb", "epodoc")
        number: Numéro du brevet

    Returns:
        Description du brevet
    """
    if not ops_client:
        return {"error": "Veuillez d'abord vous authentifier avec authenticate_ops()"}

    try:
        endpoint = f"/published-data/{reference_type}/{reference_format}/{number}/description"
        result = await ops_client.make_request(endpoint)

        return {
            "reference_type": reference_type,
            "reference_format": reference_format,
            "number": number,
            "description_data": result
        }

    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def get_patent_equivalents(
    reference_type: str,
    reference_format: str,
    number: str
) -> Dict[str, Any]:
    """
    Récupérer les équivalents d'un brevet (famille de brevets).

    Args:
        reference_type: Type de référence ("publication", "application", "priority")
        reference_format: Format de référence ("docdb", "epodoc")
        number: Numéro du brevet

    Returns:
        Équivalents/famille du brevet
    """
    if not ops_client:
        return {"error": "Veuillez d'abord vous authentifier avec authenticate_ops()"}

    try:
        endpoint = f"/published-data/{reference_type}/{reference_format}/{number}/equivalents"
        result = await ops_client.make_request(endpoint)

        return {
            "reference_type": reference_type,
            "reference_format": reference_format,
            "number": number,
            "equivalents_data": result
        }

    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def get_patent_family(
    reference_type: str,
    reference_format: str,
    number: str,
    include_biblio: bool = False
) -> Dict[str, Any]:
    """
    Récupérer la famille INPADOC d'un brevet.

    Args:
        reference_type: Type de référence ("publication", "application", "priority")
        reference_format: Format de référence ("docdb", "epodoc")
        number: Numéro du brevet
        include_biblio: Inclure les données bibliographiques pour chaque membre

    Returns:
        Famille INPADOC du brevet
    """
    if not ops_client:
        return {"error": "Veuillez d'abord vous authentifier avec authenticate_ops()"}

    try:
        endpoint = f"/family/{reference_type}/{reference_format}/{number}"
        if include_biblio:
            endpoint += "/biblio"

        result = await ops_client.make_request(endpoint)

        return {
            "reference_type": reference_type,
            "reference_format": reference_format,
            "number": number,
            "include_biblio": include_biblio,
            "family_data": result
        }

    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def get_legal_data(
    reference_type: str,
    reference_format: str,
    number: str
) -> Dict[str, Any]:
    """
    Récupérer les données légales d'un brevet.

    Args:
        reference_type: Type de référence ("publication", "application", "priority")
        reference_format: Format de référence ("docdb", "epodoc")
        number: Numéro du brevet

    Returns:
        Données légales du brevet
    """
    if not ops_client:
        return {"error": "Veuillez d'abord vous authentifier avec authenticate_ops()"}

    try:
        endpoint = f"/legal/{reference_type}/{reference_format}/{number}"
        result = await ops_client.make_request(endpoint)

        return {
            "reference_type": reference_type,
            "reference_format": reference_format,
            "number": number,
            "legal_data": result
        }

    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def search_register_data(
    query: str,
    constituent: str = "biblio",
    range_param: str = "1-25"
) -> Dict[str, Any]:
    """
    Rechercher dans les données du registre EPO.

    Args:
        query: Requête de recherche
        constituent: Type de données ("biblio", "events", "procedural-steps", "upp")
        range_param: Plage de résultats

    Returns:
        Résultats de recherche dans le registre
    """
    if not ops_client:
        return {"error": "Veuillez d'abord vous authentifier avec authenticate_ops()"}

    try:
        endpoint = f"/register/search/{constituent}" if constituent != "biblio" else "/register/search"
        params = {"q": query, "Range": range_param}

        result = await ops_client.make_request(endpoint, params)
        return {
            "query": query,
            "constituent": constituent,
            "range": range_param,
            "results": result
        }

    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def get_cpc_classification(
    cpc_class: str,
    subclass: Optional[str] = None,
    ancestors: bool = False,
    navigation: bool = False,
    depth: str = "1"
) -> Dict[str, Any]:
    """
    Récupérer les informations de classification CPC.

    Args:
        cpc_class: Classe CPC (ex: "A01B")
        subclass: Sous-classe CPC optionnelle (ex: "00")
        ancestors: Inclure les ancêtres
        navigation: Inclure la navigation
        depth: Profondeur de traversée ("0", "1", "2", "3", "all")

    Returns:
        Informations de classification CPC
    """
    if not ops_client:
        return {"error": "Veuillez d'abord vous authentifier avec authenticate_ops()"}

    try:
        if subclass:
            endpoint = f"/classification/cpc/{cpc_class}/{subclass}"
        else:
            endpoint = f"/classification/cpc/{cpc_class}"

        params = {}
        if ancestors:
            params["ancestors"] = "true"
        if navigation:
            params["navigation"] = "true"
        if depth != "1":
            params["depth"] = depth

        result = await ops_client.make_request(endpoint, params)

        return {
            "cpc_class": cpc_class,
            "subclass": subclass,
            "ancestors": ancestors,
            "navigation": navigation,
            "depth": depth,
            "classification_data": result
        }

    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def convert_patent_number(
    reference_type: str,
    input_format: str,
    number: str,
    output_format: str
) -> Dict[str, Any]:
    """
    Convertir le format d'un numéro de brevet.

    Args:
        reference_type: Type de référence ("application", "priority", "publication")
        input_format: Format d'entrée ("docdb", "epodoc", "original")
        number: Numéro à convertir
        output_format: Format de sortie ("docdb", "epodoc", "original")

    Returns:
        Numéro converti
    """
    if not ops_client:
        return {"error": "Veuillez d'abord vous authentifier avec authenticate_ops()"}

    try:
        endpoint = f"/number-service/{reference_type}/{input_format}/{number}/{output_format}"
        result = await ops_client.make_request(endpoint)

        return {
            "reference_type": reference_type,
            "input_format": input_format,
            "input_number": number,
            "output_format": output_format,
            "conversion_result": result
        }

    except Exception as e:
        return {"error": str(e)}

@mcp.resource("mcp://ops-api-help")
async def get_ops_help() -> str:
    """Guide d'utilisation de l'API OPS EPO via ce serveur MCP."""

    return """
# Guide d'utilisation du serveur MCP OPS EPO

## 1. Authentification
Avant d'utiliser les autres outils, authentifiez-vous :

**Option 1 : Utiliser les clés du fichier .env (recommandé)**
```
authenticate_ops_env()
```

**Option 2 : Passer les clés manuellement**
```
authenticate_ops(consumer_key="votre_clé", consumer_secret="votre_secret")
```

## 2. Recherche de brevets
```
search_patents(query="ti=artificial intelligence", constituent="biblio", range_param="1-10")
```

Syntaxes de recherche communes :
- `ti=mot` : Recherche dans le titre
- `ab=mot` : Recherche dans l'abrégé
- `pa=entreprise` : Recherche par déposant
- `in=inventeur` : Recherche par inventeur
- `ic=A01B` : Recherche par classification
- `pd=20200101->20201231` : Recherche par date de publication

## 3. Récupération de données de brevet
- `get_patent_biblio()` : Données bibliographiques
- `get_patent_abstract()` : Abrégé
- `get_patent_claims()` : Revendications
- `get_patent_description()` : Description complète
- `get_patent_equivalents()` : Famille de brevets

## 4. Formats de référence
- **docdb** : EP.1234567.A1 (format EPO interne)
- **epodoc** : EP1234567 (format simplifié)

## 5. Types de référence
- **publication** : Brevet publié
- **application** : Demande de brevet
- **priority** : Priorité

## 6. Exemple complet
1. `authenticate_ops_env()`  # Utilise automatiquement les clés du .env
2. `search_patents("ti=solar panel", "biblio", "1-5")`
3. `get_patent_biblio("publication", "epodoc", "EP1234567")`
4. `get_patent_family("publication", "epodoc", "EP1234567", true)`

## Ressources
- Documentation OPS : https://ops.epo.org/
- Syntaxe de recherche : https://ops.epo.org/search-syntax/
"""

if __name__ == "__main__":
    import sys

    # Configuration du serveur MCP
    mcp.run(transport="stdio")
