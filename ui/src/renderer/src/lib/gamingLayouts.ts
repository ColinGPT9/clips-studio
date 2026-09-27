/** Gaming / Reaction layouts and platform safe zones: a copy of
 *  gaming/layouts.json, which the renderer reads. A test keeps the two
 *  identical (tests/test_ui_gaming_layout_sync.py); edit the JSON, then this. */

export const LAYOUTS = {
  "canvas": [
    1080,
    1920
  ],
  "safe_zones": {
    "tiktok": {
      "label": "TikTok",
      "top": 180,
      "bottom": 420,
      "left": 60,
      "right": 190
    },
    "reels": {
      "label": "Instagram Reels",
      "top": 245,
      "bottom": 400,
      "left": 60,
      "right": 205
    },
    "shorts": {
      "label": "YouTube Shorts",
      "top": 250,
      "bottom": 350,
      "left": 60,
      "right": 190
    },
    "all": {
      "label": "All three",
      "top": 250,
      "bottom": 420,
      "left": 60,
      "right": 205
    },
    "none": {
      "label": "None",
      "top": 0,
      "bottom": 0,
      "left": 0,
      "right": 0
    }
  },
  "headroom": 0.06,
  "presets": {
    "split": {
      "label": "Split",
      "type": "stack",
      "rows": [
        [
          "cam"
        ],
        [
          "game"
        ]
      ],
      "divider": [
        0.25,
        0.5,
        0.38
      ],
      "order": "cam_top",
      "game_fit": "fill"
    },
    "basecam": {
      "label": "Basecam",
      "type": "stack",
      "rows": [
        [
          "cam"
        ],
        [
          "game"
        ]
      ],
      "divider": [
        0.25,
        0.5,
        0.38
      ],
      "order": "game_top",
      "game_fit": "fill"
    },
    "half": {
      "label": "Half",
      "type": "stack",
      "rows": [
        [
          "cam"
        ],
        [
          "game"
        ]
      ],
      "divider": [
        0.5,
        0.5,
        0.5
      ],
      "order": "cam_top",
      "game_fit": "fill"
    },
    "fullscreen": {
      "label": "Fullscreen",
      "type": "full",
      "game_fit": "fill"
    },
    "blurred": {
      "label": "Blurred",
      "type": "full",
      "game_fit": "fit"
    },
    "small_cam": {
      "label": "Small facecam",
      "type": "pip",
      "cams": [
        "cam"
      ],
      "pip_width": 0.42,
      "pip_aspect": 1.6,
      "shape": "rect",
      "game_fit": "fill"
    },
    "circle_cam": {
      "label": "Circle facecam",
      "type": "pip",
      "cams": [
        "cam"
      ],
      "pip_width": 0.38,
      "pip_aspect": 1.0,
      "shape": "circle",
      "game_fit": "fill"
    },
    "game_ui": {
      "label": "Game UI",
      "type": "stack",
      "rows": [
        [
          "cam"
        ],
        [
          "game"
        ]
      ],
      "overlay": [
        "ui"
      ],
      "divider": [
        0.25,
        0.45,
        0.33
      ],
      "ui_share": 0.12,
      "order": "cam_top",
      "game_fit": "fill"
    },
    "mosaic": {
      "label": "Mosaic",
      "type": "stack",
      "rows": [
        [
          "cam",
          "ui"
        ],
        [
          "game"
        ]
      ],
      "divider": [
        0.2,
        0.4,
        0.28
      ],
      "order": "cam_top",
      "game_fit": "fill"
    },
    "dual_cam": {
      "label": "Dual facecam",
      "type": "pip",
      "cams": [
        "cam",
        "cam2"
      ],
      "pip_width": 0.38,
      "pip_aspect": 1.0,
      "shape": "circle",
      "game_fit": "fill"
    },
    "duo_split": {
      "label": "Duo split",
      "type": "stack",
      "rows": [
        [
          "cam",
          "cam2"
        ],
        [
          "game"
        ]
      ],
      "divider": [
        0.25,
        0.45,
        0.33
      ],
      "order": "cam_top",
      "game_fit": "fill"
    }
  }
} as const

export type PresetId = keyof typeof LAYOUTS.presets
export type SafeId = keyof typeof LAYOUTS.safe_zones
