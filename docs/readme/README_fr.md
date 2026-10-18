<div align="center">

[English](../../README.md) · [简体中文](README_zh.md) · [日本語](README_ja.md) · [한국어](README_ko.md) · [Türkçe](README_tr.md) · [Русский](README_ru.md) · [Tiếng Việt](README_vi.md) · [ไทย](README_th.md) · [Deutsch](README_de.md) · [Español](README_es.md) · **Français** · [Português (Brasil)](README_pt-BR.md) · [العربية](README_ar.md) · [Bahasa Indonesia](README_id.md) · [Italiano](README_it.md) · [हिन्दी](README_hi.md) · [বাংলা](README_bn.md) · [Filipino](README_tl.md) · [اردو](README_ur.md)

</div>

# Clips Kitty : l'alternative gratuite et open source à Opus Clip qui tourne sur votre propre PC

<a href="https://apps.microsoft.com/detail/9NB6XT7DSQZZ">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://get.microsoft.com/images/en-us%20dark.svg">
    <img alt="Obtenir Clips Kitty sur le Microsoft Store" src="https://get.microsoft.com/images/en-us%20light.svg" height="52">
  </picture>
</a>

[![Dernière version](https://badgen.net/github/tag/ColinGPT9/clips-studio?label=release)](https://github.com/ColinGPT9/clips-studio/releases)
[![Téléchargements](https://img.shields.io/github/downloads/ColinGPT9/clips-studio/total?label=downloads)](https://github.com/ColinGPT9/clips-studio/releases)
[![Étoiles](https://badgen.net/github/stars/ColinGPT9/clips-studio?label=stars)](https://github.com/ColinGPT9/clips-studio/stargazers)
[![Licence](https://img.shields.io/github/license/ColinGPT9/clips-studio)](../../LICENSE)

> Ceci est une version traduite abrégée. La documentation complète se trouve dans le
> [README en anglais](../../README.md).

### Mettez une étoile à Clips Kitty et partagez-le ⭐

**Merci de mettre une étoile au dépôt et de le partager.** Ça ne vous coûte rien, et il n'y a
aucun budget marketing derrière ce projet : le bouche-à-oreille est donc le seul moyen pour que le prochain petit
youtubeur ou streamer découvre qu'une option gratuite existe.

**Je l'ai créé pour les petits youtubeurs et streamers** qui n'ont pas les moyens de payer les autres
solutions ou un monteur. C'est une alternative gratuite à Opus Clip, Vizard et Klap qui
fait tourner **Google Gemma** sur votre propre PC : pas de frais de serveur, pas de frais par clip, pas de frais
d'utilisation de l'IA.

<img src="../brand/mascot.png" alt="" width="150" align="right">

**Transformez vos longs streams et vidéos en Shorts, Reels et TikToks prêts à publier,
entièrement sur votre propre machine.** Collez un lien YouTube, Twitch ou Kick : l'outil repère
les meilleurs moments, les recadre en 9:16 en gardant la personne qui parle au centre, incruste
des sous-titres synchronisés mot à mot et rédige titres, descriptions et hashtags.

Pas d'IA dans le cloud par défaut. Pas d'abonnement. Pas de frais par clip. Vos vidéos ne quittent jamais
votre ordinateur, sauf si vous lui demandez de publier un clip sur vos propres comptes.

<img src="../store-screenshots/fs-dashboard-202754.png" width="100%"
     alt="Le tableau de bord de Clips Kitty, avec une vidéo terminée : 38 clips produits en 19 minutes.">

<sub>38 clips tirés d'une seule vidéo en 19 minutes, sur un seul PC, sans rien mettre en ligne.</sub>

## Ce qu'il fait

- **Trouve les meilleurs moments** : ce qui est dit, l'intensité de l'audio, ce qui se passe
  à l'écran et la force de l'accroche sont notés ensemble. Chaque clip qui atteint
  le niveau de qualité est conservé.
- **Discussion, gaming et sport** : conçu pour l'IRL, le just chatting et les podcasts ; le mode
  **Gaming / Reaction** (Gaming / Réaction) place la webcam et le jeu chacun dans sa moitié ; le mode
  **Sports** (Sport) extrait les buts, les arrêts et les grosses occasions d'un match.
- **Garde la personne qui parle dans le cadre** : le suivi du visage suit celui ou celle qui parle vraiment.
- **Sous-titres modifiables** : synchronisés mot à mot, stylés comme vous le voulez, corrigeables ligne par ligne.
- **Titres, descriptions et hashtags** rédigés pour vous, tous modifiables.
- **Publication automatique (facultative)** : surveillez une chaîne, découpez chaque nouvelle vidéo et publiez
  automatiquement sur YouTube, TikTok et Instagram, sur vos propres comptes.
- **19 langues** : l'application est traduite en 19 langues, et les clips peuvent être
  traduits, sous-titrés et doublés dans chacune d'elles.

## Configuration requise

|         | Minimum                                       | Recommandé               |
| ------- | --------------------------------------------- | ------------------------ |
| **GPU** | NVIDIA RTX 2060 (6 Go)                        | RTX 3060 12 Go ou mieux  |
| **CPU** | Intel Core i3-9100 / AMD Ryzen 3 3200G (2019) | 6 cœurs ou plus          |
| **RAM** | 16 Go                                         | 16 Go ou plus            |
| **OS**  | Windows 10 ou 11                              |                          |

Une carte graphique est facultative : sans elle, tout tourne sur le processeur, plus lentement
mais intégralement.

## Installation

- **[Microsoft Store](https://apps.microsoft.com/detail/9NB6XT7DSQZZ)** : un clic,
  et il se met à jour tout seul.
- **Ou l'installateur** : le dernier **Web Setup** depuis
  [Releases](https://github.com/ColinGPT9/clips-studio/releases).
- **Ou winget** : `winget install ColinGPT9.ClipsKitty`

L'assistant d'installation télécharge le modèle d'IA adapté à votre carte graphique. Ensuite,
tout fonctionne hors ligne.

## Aidez à traduire Clips Kitty

Le mainteneur ne parle qu'anglais, la traduction en français dépend donc des
personnes qui le parlent. **Signaler un seul mot faux ou maladroit est une contribution
à part entière**, sans pull request : [comment aider](https://github.com/ColinGPT9/clips-studio/issues/55).
Cette page peut elle aussi contenir des erreurs : les corrections sont les bienvenues.

## Contribuer

Les contributions sont les bienvenues. Consultez **[CONTRIBUTING.md](../../CONTRIBUTING.md)** pour
commencer. Les issues étiquetées
[`good-first-issue`](https://github.com/ColinGPT9/clips-studio/labels/good-first-issue)
sont petites et bien délimitées.

## Licence

[AGPL-3.0](../../LICENSE). Gratuit à utiliser, à modifier et à partager.
