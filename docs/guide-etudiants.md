# Utiliser Scholar dans Claude ou Codex

Scholar cherche des articles scientifiques dans OpenAlex, Semantic Scholar,
Crossref, Europe PMC, NASA ADS, CORE et Scopus en une seule requête. Votre
enseignant vous donne l'adresse du site, par exemple
`https://scholar.example.org`.

## 1. Ajouter vos clés API (5 minutes)

1. Ouvrez l'adresse du site et cliquez sur **Se connecter**. Utilisez votre
   compte universitaire.
2. Sur la page **Mes clés API**, collez les clés que vous avez. Chaque champ a
   un lien **Obtenir une clé**. Commencez par :
   - **OpenAlex** (gratuite, la plus importante);
   - **Semantic Scholar** (gratuite, sur demande; facultative).
3. Cliquez sur **Enregistrer**.

Vos clés sont chiffrées et ne servent qu'à vos propres recherches. Sans clé,
une source est simplement ignorée; Crossref et Europe PMC fonctionnent
toujours.

## 2. Ajouter le connecteur

**Claude** (application de bureau, web ou mobile) :

1. **Customize > Connectors**, puis **+ Add > Add custom connector**.
2. Nom : `Scholar`. URL : l'adresse du site suivie de `/mcp`, par exemple
   `https://scholar.example.org/mcp`.
3. Cliquez sur **Connect** et connectez-vous avec le même compte qu'à
   l'étape 1. Si Claude propose plusieurs options d'identification,
   choisissez **Register automatically**.

Le plan gratuit de Claude permet un seul connecteur personnalisé.

**Codex** : ajoutez ces lignes dans `~/.codex/config.toml`

```toml
[mcp_servers.scholar]
url = "https://scholar.example.org/mcp"
```

puis lancez `codex mcp login scholar` et connectez-vous.

## 3. Essayer

Demandez par exemple : « Cherche avec Scholar des articles récents sur
l'albédo des glaciers et le carbone suie ». Pour voir quelles sources sont
actives avec vos clés, demandez : « Quel est le statut des API Scholar ? »

## En cas de problème

- **Accès refusé** : votre compte n'est pas dans la liste de votre
  enseignant. Utilisez votre compte universitaire ou écrivez-lui.
- **Aucun résultat d'une source** : vérifiez la clé sur la page Mes clés.
- **Scopus** demande souvent d'être sur le réseau de l'université et peut ne
  pas fonctionner depuis le serveur.
