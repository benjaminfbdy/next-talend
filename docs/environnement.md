# Environnement de développement

Poste Windows 11 (perso). Tout est installé hors des dossiers du dépôt, sauf le workspace Talend.

| Composant | Version | Emplacement | Source |
|---|---|---|---|
| JDK | Temurin 21.0.12 LTS | `C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot` | `winget install EclipseAdoptium.Temurin.21.JDK` |
| PostgreSQL | 17.11 | `C:\Program Files\PostgreSQL\17` (service `postgresql-x64-17`, port 5432) | `winget install PostgreSQL.PostgreSQL.17` |
| Talaxie Studio (DI) | 8.9.0 — build 202610 (`20261001_1724`) | `C:\Talaxie\8.9.0` | https://deilink.fr/#/download |
| Workspace Talend | — | `talend-workspace/` (dans ce dépôt) | `scripts/launch-studio.ps1` |
| Python | 3.14 | — | stdlib uniquement |

## Pourquoi Talaxie

Talend Open Studio a été retiré fin janvier 2024. Talaxie est un fork communautaire gratuit **basé sur Talend 8.0** : même interface, mêmes composants (`tFileInputDelimited`, `tMap`, `tDBOutput`, `tRunJob`…), même génération de code Java. Les jobs construits ici se transposent donc à Talend Studio.

## Java : version exigée

Le `TOS_DI.ini` de Talaxie 8.9.0 impose `-Dosgi.requiredJavaVersion=21` : **JDK 21 obligatoire**. Sans Java détectable, le lanceur affiche « A Java Runtime Environment (JRE) or Java Development Kit (JDK) must be available… » ; juste après l'installation du JDK, une session déjà ouverte n'a pas le nouveau `PATH` — `scripts/launch-studio.ps1` le relit depuis le registre.

## Téléchargement Talaxie (traçabilité)

- Fichier : `TOS_DI-20261001_1724-V8.9.0SNAPSHOT-win32.win32.x86_64.zip` (1 765 522 866 octets)
- SHA-256 : `8354FAE034833B41B26C542B41DB2CFAD406EA5C6DDDB6B47959E87EB531E750`
- Il n'existe pas de release GitHub : le binaire est distribué par le partenaire DeiLink (le site renvoie les URL `https://deilink.fr/deilink/TOS_DI-…`). Alternatives sur la même page : `202511` (Java 21, test) et `8.8.8` (juillet 2024).
- Extraction : `ZipFile.ExtractToDirectory` (.NET). Le `tar` de Git Bash (GNU) ne lit pas les zip ; le `tar` de Windows (bsdtar) le peut.

## Base de données

- Base : `assurance_poc` (UTF8). Les identifiants vivent dans `.env` (ignoré par git) ; modèle : `.env.example`.
- Pour la suite, les jobs n'utiliseront **pas** le superutilisateur `postgres` : un rôle applicatif dédié, avec droits restreints par schéma (`stg`, `rej`, `dwh`, `arc`, `log`), sera créé à l'étape 2.
