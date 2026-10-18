<div align="center">

[English](../../README.md) · [简体中文](README_zh.md) · **日本語** · [한국어](README_ko.md) · [Türkçe](README_tr.md) · [Русский](README_ru.md) · [Tiếng Việt](README_vi.md) · [ไทย](README_th.md) · [Deutsch](README_de.md) · [Español](README_es.md) · [Français](README_fr.md) · [Português (Brasil)](README_pt-BR.md) · [العربية](README_ar.md) · [Bahasa Indonesia](README_id.md) · [Italiano](README_it.md) · [हिन्दी](README_hi.md) · [বাংলা](README_bn.md) · [Filipino](README_tl.md) · [اردو](README_ur.md)

</div>

# Clips Kitty：自分の PC で動く、無料でオープンソースの Opus Clip 代替ツール

<a href="https://apps.microsoft.com/detail/9NB6XT7DSQZZ">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://get.microsoft.com/images/en-us%20dark.svg">
    <img alt="Microsoft Store から Clips Kitty を入手" src="https://get.microsoft.com/images/en-us%20light.svg" height="52">
  </picture>
</a>

[![最新リリース](https://badgen.net/github/tag/ColinGPT9/clips-studio?label=release)](https://github.com/ColinGPT9/clips-studio/releases)
[![ダウンロード数](https://img.shields.io/github/downloads/ColinGPT9/clips-studio/total?label=downloads)](https://github.com/ColinGPT9/clips-studio/releases)
[![スター](https://badgen.net/github/stars/ColinGPT9/clips-studio?label=stars)](https://github.com/ColinGPT9/clips-studio/stargazers)
[![ライセンス](https://img.shields.io/github/license/ColinGPT9/clips-studio)](../../LICENSE)

> これは短い翻訳版です。完全なドキュメントは[英語版 README](../../README.md) をご覧ください。

### Clips Kitty にスターを付けて、シェアしてください ⭐

**リポジトリにスターを付けて、シェアしていただけると嬉しいです。** お金は一切かかりません。このプロジェクトには宣伝予算がないので、次の小さな YouTuber や配信者が無料の選択肢を知る方法は口コミだけなんです。

**小規模な YouTuber や配信者のために作りました。** ほかのツールや編集者に払うお金がない人たちのためです。Opus Clip、Vizard、Klap の無料の代わりになるツールで、**Google Gemma** を自分の PC で動かします。サーバー代も、クリップごとの料金も、AI の利用料もかかりません。

<img src="../brand/mascot.png" alt="" width="150" align="right">

**長い配信や動画を、すぐに投稿できる Shorts、Reels、TikTok に。すべて自分のパソコンの中で完結します。** YouTube、Twitch、Kick のリンクを貼り付けるだけで、見どころを見つけ、話している人を中央に保ったまま 9:16 に切り抜き、単語ごとに同期した字幕を焼き込み、タイトル・説明文・ハッシュタグまで書いてくれます。

初期設定ではクラウド AI を使いません。サブスクリプションなし。クリップごとの料金なし。自分のアカウントへのクリップ投稿を頼まない限り、映像がパソコンの外に出ることはありません。

<img src="../store-screenshots/fs-dashboard-202754.png" width="100%"
     alt="Clips Kitty のダッシュボード。処理が終わった動画から 19 分で 38 本のクリップが作られた様子。">

<sub>1 本の動画から 19 分で 38 本のクリップ。PC 1 台で、何もアップロードせずに。</sub>

## できること

- **見どころを見つける**：何が話されているか、音声がどれだけ盛り上がっているか、画面で何が起きているか、冒頭のつかみがどれだけ強いかをまとめて評価します。品質の基準を超えたクリップはすべて残します。
- **雑談、ゲーム、スポーツ**：IRL、雑談配信、ポッドキャスト向けに作られています。**Gaming / Reaction**（ゲーム / リアクション）モードではウェブカメラとゲーム画面をそれぞれ半分ずつに配置し、**Sports**（スポーツ）モードでは試合からゴール、セーブ、決定機を切り抜きます。
- **話している人を画面に収める**：顔トラッキングが、実際に話している人を追いかけます。
- **編集できる字幕**：単語ごとに同期し、スタイルは自由、1 行ずつ修正できます。
- **タイトル、説明文、ハッシュタグ**を自動で作成。すべて編集できます。
- **ハンズフリー投稿（任意）**：チャンネルを見守り、新しい動画が出るたびにクリップを作って、自分の YouTube、TikTok、Instagram アカウントに自動で投稿します。
- **19 言語**：アプリは 19 言語に翻訳されており、クリップもそれらの言語に翻訳、字幕付け、吹き替えができます。

## 動作環境

|         | 最小                                          | 推奨                     |
| ------- | --------------------------------------------- | ------------------------ |
| **GPU** | NVIDIA RTX 2060 (6 GB)                        | RTX 3060 12 GB 以上      |
| **CPU** | Intel Core i3-9100 / AMD Ryzen 3 3200G (2019) | 6 コア以上               |
| **RAM** | 16 GB                                         | 16 GB 以上               |
| **OS**  | Windows 10 または 11                          |                          |

グラフィックカードは必須ではありません。なくてもすべてプロセッサーで動きます。遅くはなりますが、機能はすべて使えます。

## インストール

- **[Microsoft Store](https://apps.microsoft.com/detail/9NB6XT7DSQZZ)**：ワンクリックで入り、自動で更新されます。
- **またはインストーラー**：[Releases](https://github.com/ColinGPT9/clips-studio/releases) から最新の **Web Setup** を入手。
- **または winget**：`winget install ColinGPT9.ClipsKitty`

セットアップウィザードが、お使いのグラフィックカードに合った AI モデルをダウンロードします。その後はオフラインで動きます。

## Clips Kitty の翻訳を手伝ってください

メンテナーは英語しか話せないので、日本語の翻訳は日本語を話す皆さんが頼りです。**おかしな言葉や不自然な言葉を 1 つ報告するだけでも、立派な貢献です。** pull request は必要ありません：[手伝い方](https://github.com/ColinGPT9/clips-studio/issues/58)。このページにも間違いがあるかもしれません。修正は大歓迎です。

## コントリビュート

コントリビュートは大歓迎です。まずは **[CONTRIBUTING.md](../../CONTRIBUTING.md)** をご覧ください。[`good-first-issue`](https://github.com/ColinGPT9/clips-studio/labels/good-first-issue) ラベルの付いた issue は小さく、範囲がはっきりしています。

## ライセンス

[AGPL-3.0](../../LICENSE)。自由に使い、変更し、共有できます。
