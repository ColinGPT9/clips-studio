<div align="center">

[English](../../README.md) · **简体中文** · [日本語](README_ja.md) · [한국어](README_ko.md) · [Türkçe](README_tr.md) · [Русский](README_ru.md) · [Tiếng Việt](README_vi.md) · [ไทย](README_th.md) · [Deutsch](README_de.md) · [Español](README_es.md) · [Français](README_fr.md) · [Português (Brasil)](README_pt-BR.md) · [العربية](README_ar.md) · [Bahasa Indonesia](README_id.md) · [Italiano](README_it.md) · [हिन्दी](README_hi.md) · [বাংলা](README_bn.md) · [Filipino](README_tl.md) · [اردو](README_ur.md)

</div>

# Clips Kitty：免费、开源、在你自己电脑上运行的 Opus Clip 替代品

<a href="https://apps.microsoft.com/detail/9NB6XT7DSQZZ">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://get.microsoft.com/images/en-us%20dark.svg">
    <img alt="从 Microsoft Store 获取 Clips Kitty" src="https://get.microsoft.com/images/en-us%20light.svg" height="52">
  </picture>
</a>

[![最新版本](https://badgen.net/github/tag/ColinGPT9/clips-studio?label=release)](https://github.com/ColinGPT9/clips-studio/releases)
[![下载量](https://img.shields.io/github/downloads/ColinGPT9/clips-studio/total?label=downloads)](https://github.com/ColinGPT9/clips-studio/releases)
[![星标](https://badgen.net/github/stars/ColinGPT9/clips-studio?label=stars)](https://github.com/ColinGPT9/clips-studio/stargazers)
[![许可证](https://img.shields.io/github/license/ColinGPT9/clips-studio)](../../LICENSE)

> 这是一份简短的翻译版本。完整文档请见[英文 README](../../README.md)。

### 为 Clips Kitty 点星并分享 ⭐

**请为这个仓库点个星，并分享给更多人。** 这不花你一分钱。这个项目没有任何营销预算，口口相传是让下一位小 YouTuber 或主播知道还有免费选择的唯一方式。

**我是为小 YouTuber 和主播做的这个工具**，为那些负担不起其他方案、也请不起剪辑师的人。它是免费的 Opus Clip、Vizard 和 Klap 替代品，在你自己的电脑上运行 **Google Gemma**：没有服务器费用，没有按片段收费，也没有 AI 使用费。

<img src="../brand/mascot.png" alt="" width="150" align="right">

**把长直播和长视频变成可以直接发布的 Shorts、Reels 和 TikTok 短视频，全程只在你自己的电脑上完成。** 粘贴一个 YouTube、Twitch 或 Kick 链接，它就会找出最精彩的片段，裁成 9:16 并让说话的人保持在画面中央，烧录逐字同步的字幕，还会写好标题、简介和话题标签。

默认不使用云端 AI。没有订阅。不按片段收费。你的素材永远不会离开你的电脑，除非你让它把片段发布到你自己的账号。

<img src="../store-screenshots/fs-dashboard-202754.png" width="100%"
     alt="Clips Kitty 的控制面板，显示一个已完成的视频在 19 分钟内生成了 38 个片段。">

<sub>一个视频，19 分钟，38 个片段，只用一台电脑，什么都没有上传。</sub>

## 它能做什么

- **找出最精彩的时刻**：综合评估说了什么、音频有多激动、画面上发生了什么，以及开头的吸引力有多强。每个达到质量标准的片段都会保留。
- **聊天、游戏和体育**：专为户外直播、闲聊和播客打造；**Gaming / Reaction**（游戏 / 反应）模式把摄像头画面和游戏画面各放一半；**Sports**（体育）模式从比赛中剪出进球、扑救和绝佳机会。
- **让说话的人始终在画面里**：面部追踪会跟随真正在说话的那个人。
- **可编辑的字幕**：逐字同步，样式随你设置，可以逐行修改。
- **标题、简介和话题标签**都会帮你写好，全部可以编辑。
- **自动发布（可选）**：关注一个频道，自动剪辑每个新视频，并发布到你自己的 YouTube、TikTok 和 Instagram 账号。
- **19 种语言**：应用已翻译成 19 种语言，片段也可以翻译、加字幕并配音成这些语言。

## 系统要求

|         | 最低配置                                      | 推荐配置                 |
| ------- | --------------------------------------------- | ------------------------ |
| **GPU** | NVIDIA RTX 2060 (6 GB)                        | RTX 3060 12 GB 或更好    |
| **CPU** | Intel Core i3-9100 / AMD Ryzen 3 3200G (2019) | 6 核或更多               |
| **RAM** | 16 GB                                         | 16 GB 或更多             |
| **OS**  | Windows 10 或 11                              |                          |

显卡不是必需的：没有显卡时，一切都在处理器上运行，速度较慢，但功能完整。

## 安装

- **[Microsoft Store](https://apps.microsoft.com/detail/9NB6XT7DSQZZ)**：一键安装，并会自动更新。
- **或者使用安装程序**：从 [Releases](https://github.com/ColinGPT9/clips-studio/releases) 下载最新的 **Web Setup**。
- **或者使用 winget**：`winget install ColinGPT9.ClipsKitty`

安装向导会下载适合你显卡的 AI 模型。之后它就可以离线运行。

## 帮忙翻译 Clips Kitty

维护者只会说英语，所以简体中文翻译要靠会说这门语言的人。**报告一个错误或别扭的词，就是一次完整的贡献**，不需要提交 pull request：[如何帮忙](../TRANSLATING.md)。这个页面也可能有错误，非常欢迎指正。

## 参与贡献

欢迎贡献。请参阅 **[CONTRIBUTING.md](../../CONTRIBUTING.md)** 开始。标有 [`good-first-issue`](https://github.com/ColinGPT9/clips-studio/labels/good-first-issue) 的 issue 规模小、范围清晰。

## 许可证

[AGPL-3.0](../../LICENSE)。可以自由使用、修改和分享。
