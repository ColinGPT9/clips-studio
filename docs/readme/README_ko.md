<div align="center">

[English](../../README.md) · [简体中文](README_zh.md) · [日本語](README_ja.md) · **한국어** · [Türkçe](README_tr.md) · [Русский](README_ru.md) · [Tiếng Việt](README_vi.md) · [ไทย](README_th.md) · [Deutsch](README_de.md) · [Español](README_es.md) · [Français](README_fr.md) · [Português (Brasil)](README_pt-BR.md) · [العربية](README_ar.md) · [Bahasa Indonesia](README_id.md) · [Italiano](README_it.md) · [हिन्दी](README_hi.md) · [বাংলা](README_bn.md) · [Filipino](README_tl.md) · [اردو](README_ur.md)

</div>

# Clips Kitty: 내 PC에서 돌아가는 무료 오픈 소스 Opus Clip 대안

<a href="https://apps.microsoft.com/detail/9NB6XT7DSQZZ">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://get.microsoft.com/images/en-us%20dark.svg">
    <img alt="Microsoft Store에서 Clips Kitty 받기" src="https://get.microsoft.com/images/en-us%20light.svg" height="52">
  </picture>
</a>

[![최신 릴리스](https://badgen.net/github/tag/ColinGPT9/clips-studio?label=release)](https://github.com/ColinGPT9/clips-studio/releases)
[![다운로드](https://img.shields.io/github/downloads/ColinGPT9/clips-studio/total?label=downloads)](https://github.com/ColinGPT9/clips-studio/releases)
[![스타](https://badgen.net/github/stars/ColinGPT9/clips-studio?label=stars)](https://github.com/ColinGPT9/clips-studio/stargazers)
[![라이선스](https://img.shields.io/github/license/ColinGPT9/clips-studio)](../../LICENSE)

> 이 문서는 짧게 번역한 버전입니다. 전체 문서는
> [영어 README](../../README.md)에 있습니다.

### Clips Kitty에 스타를 누르고 공유해 주세요 ⭐

**저장소에 스타를 누르고 공유해 주세요.** 비용은 전혀 들지 않습니다. 이 프로젝트에는
마케팅 예산이 없어서, 다음 소규모 유튜버나 스트리머가 무료 선택지가 있다는 걸 알게 되는
방법은 입소문뿐입니다.

**소규모 유튜버와 스트리머를 위해 만들었습니다.** 다른 서비스나 편집자를 쓸 여유가 없는
분들을 위해서요. Opus Clip, Vizard, Klap을 대신할 수 있는 무료 도구로, **Google Gemma**를
내 PC에서 직접 실행합니다. 서버 비용도, 클립당 요금도, AI 사용료도 없습니다.

<img src="../brand/mascot.png" alt="" width="150" align="right">

**긴 방송과 영상을 바로 올릴 수 있는 Shorts, Reels, TikTok으로 바꿔 드립니다. 모든 작업은
내 컴퓨터 안에서만 이루어집니다.** YouTube, Twitch, Kick 링크를 붙여 넣으면 가장 좋은 순간을
찾아내고, 말하는 사람을 가운데에 둔 채 9:16으로 자르고, 단어 단위로 맞춘 자막을 입히고,
제목과 설명, 해시태그까지 써 줍니다.

기본적으로 클라우드 AI를 쓰지 않습니다. 구독도 없습니다. 클립당 요금도 없습니다. 내 계정에
클립을 올려 달라고 직접 요청하지 않는 한, 영상은 절대 내 컴퓨터 밖으로 나가지 않습니다.

<img src="../store-screenshots/fs-dashboard-202754.png" width="100%"
     alt="Clips Kitty 대시보드. 처리가 끝난 영상 하나에서 19분 만에 클립 38개가 만들어진 모습.">

<sub>영상 하나에서 19분 만에 클립 38개. PC 한 대로, 아무것도 업로드하지 않고.</sub>

## 주요 기능

- **가장 좋은 순간 찾기**: 무슨 말을 하는지, 오디오가 얼마나 신나는지, 화면에서 무슨 일이
  일어나는지, 도입부가 얼마나 강한지를 함께 점수로 매깁니다. 품질 기준을 넘는 클립은 모두 남깁니다.
- **토크, 게임, 스포츠**: IRL, 저스트 채팅, 팟캐스트에 맞춰 만들었습니다.
  **Gaming / Reaction**(게임 / 리액션) 모드는 웹캠과 게임 화면을 반씩 나눠 배치하고,
  **Sports**(스포츠) 모드는 경기에서 골, 선방, 결정적 기회를 잘라 냅니다.
- **말하는 사람을 화면 안에**: 얼굴 추적이 실제로 말하고 있는 사람을 따라갑니다.
- **편집 가능한 자막**: 단어 단위로 맞춰지고, 원하는 스타일로 꾸밀 수 있으며, 한 줄씩 고칠 수 있습니다.
- **제목, 설명, 해시태그**를 대신 써 드리며, 모두 수정할 수 있습니다.
- **자동 업로드(선택)**: 채널을 지켜보다가 새 영상마다 클립을 만들어 내 YouTube, TikTok,
  Instagram 계정에 알아서 올립니다.
- **19개 언어**: 앱이 19개 언어로 번역되어 있고, 클립도 이 언어들로 번역, 자막, 더빙할 수
  있습니다.

## 요구 사양

|         | 최소                                          | 권장                     |
| ------- | --------------------------------------------- | ------------------------ |
| **GPU** | NVIDIA RTX 2060 (6 GB)                        | RTX 3060 12 GB 이상      |
| **CPU** | Intel Core i3-9100 / AMD Ryzen 3 3200G (2019) | 6코어 이상               |
| **RAM** | 16 GB                                         | 16 GB 이상               |
| **OS**  | Windows 10 또는 11                            |                          |

그래픽 카드는 없어도 됩니다. 없으면 모든 작업이 프로세서에서 실행되어 느리지만, 기능은 모두
그대로입니다.

## 설치

- **[Microsoft Store](https://apps.microsoft.com/detail/9NB6XT7DSQZZ)**: 클릭 한 번이면 되고,
  업데이트도 알아서 됩니다.
- **또는 설치 프로그램**:
  [Releases](https://github.com/ColinGPT9/clips-studio/releases)에서 최신 **Web Setup**을 받으세요.
- **또는 winget**: `winget install ColinGPT9.ClipsKitty`

설치 마법사가 그래픽 카드에 맞는 AI 모델을 내려받습니다. 그다음부터는 오프라인으로
실행됩니다.

## Clips Kitty 번역을 도와주세요

관리자는 영어만 할 줄 알아서, 한국어 번역은 한국어를 쓰는 분들의 도움에
달려 있습니다. **어색하거나 틀린 단어 하나를 알려 주시는 것만으로도 충분한
기여입니다.** pull request는 필요 없습니다: [도와주는 방법](../TRANSLATING.md).
이 페이지에도 실수가 있을 수 있으니, 바로잡아 주시면 정말 감사하겠습니다.

## 기여하기

기여는 언제나 환영합니다. 시작하려면 **[CONTRIBUTING.md](../../CONTRIBUTING.md)** 문서를 읽어 보세요.
[`good-first-issue`](https://github.com/ColinGPT9/clips-studio/labels/good-first-issue)
라벨이 붙은 이슈는 작고 범위가 명확합니다.

## 라이선스

[AGPL-3.0](../../LICENSE). 자유롭게 사용하고, 고치고, 공유할 수 있습니다.
