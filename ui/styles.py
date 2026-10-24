"""
Cyberpunk / Neon Minimal Design System for NeonRoute
Dark obsidian palette with neon cyan, neon magenta, and lime accents.
"""

NEON_THEME_QSS = """
/* Global Window & Typography */
QWidget {
    background-color: #0A0D14;
    color: #E2E8F0;
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
    font-size: 13px;
    selection-background-color: #00F0FF;
    selection-color: #000000;
}

/* Main Window */
QMainWindow {
    background: transparent;
}

#CentralWidget, #MainContentContainer {
    background: transparent;
}

/* Left Sidebar (Glassmorphism / Frosted Glass) */
#SidebarWidget {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(16, 21, 33, 0.92), stop:1 rgba(13, 17, 26, 0.88));
    border-right: 1px solid rgba(255, 255, 255, 0.08);
}

/* Central Chat Area (Transparent for wallpaper support) */
ChatBackgroundViewport {
    background: transparent;
    background-color: transparent;
    border: none;
}
#ChatScrollArea {
    background: transparent;
    border: none;
}
#ChatScrollArea > QWidget > QWidget {
    background: transparent;
}
#ChatContainer {
    background: transparent;
}
ChatMessageWidget {
    background: transparent;
}

/* Message Cards (ChatMessageWidget / Bubbles) */
#BubbleContainer, #bubble_frame {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(28, 36, 54, 0.92), stop:1 rgba(18, 23, 35, 0.92));
    border-top: 1px solid rgba(255, 255, 255, 0.15);
    border-left: 1px solid rgba(255, 255, 255, 0.05);
    border-right: 1px solid rgba(255, 255, 255, 0.05);
    border-bottom: 1px solid rgba(0, 0, 0, 0.5);
    border-radius: 10px;
    padding: 8px 12px;
}

#BubbleContainer[is_user="true"], #bubble_frame[is_user="true"] {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(0, 110, 130, 0.90), stop:1 rgba(0, 70, 90, 0.90));
    border-top: 1px solid rgba(0, 240, 255, 0.4);
    border-left: 1px solid rgba(0, 240, 255, 0.15);
    border-right: 1px solid rgba(0, 240, 255, 0.15);
    border-bottom: 1px solid rgba(0, 0, 0, 0.5);
    border-radius: 10px;
    padding: 8px 12px;
}

/* Chat Header Bar */
#ChatHeaderBar {
    background: rgba(18, 24, 38, 0.78);
    border-bottom: 1px solid rgba(0, 240, 255, 0.18);
    padding: 6px 14px;
}

/* Input Area Container (Glassmorphism Floating Card) */
#InputAreaContainer {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(22, 28, 44, 0.94), stop:1 rgba(13, 17, 28, 0.96));
    border-top: 1px solid rgba(255, 255, 255, 0.18);
    border-left: 1px solid rgba(255, 255, 255, 0.08);
    border-right: 1px solid rgba(255, 255, 255, 0.08);
    border-bottom: 1px solid rgba(0, 0, 0, 0.6);
    border-radius: 14px;
    margin: 8px 12px;
    padding: 6px 10px;
}

/* Chat Input Field (Seamless Sunken Well Effect) */
#ChatInputField, ChatInputBox {
    background-color: rgba(10, 15, 26, 0.75);
    color: #F8FAFC;
    border: none;
    border-radius: 11px;
    font-size: 14px;
    outline: none;
}
#ChatInputField:hover:!focus, ChatInputBox:hover:!focus {
    background-color: rgba(13, 20, 34, 0.82);
    border: none;
    outline: none;
}
#ChatInputField:focus, ChatInputBox:focus {
    background-color: rgba(15, 23, 42, 0.92);
    border: none;
    outline: none;
}

/* Standard Buttons */
QPushButton {
    background-color: #1A2234;
    color: #E2E8F0;
    border: 1px solid #2A3650;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #232E47;
    border-color: #00F0FF;
    color: #FFFFFF;
}
QPushButton:pressed {
    background-color: #131A29;
    border-color: #00D2DF;
}
QPushButton:disabled {
    background-color: #121622;
    color: #55627A;
    border-color: #1C2333;
}

/* Light Icon Action Buttons (Attach, Mic & Emoji) */
QPushButton#AttachButton, QPushButton#MicButton, QPushButton#EmojiButton {
    min-width: 36px;
    min-height: 36px;
    max-width: 36px;
    max-height: 36px;
    background-color: rgba(255, 255, 255, 0.05);
    color: #CBD5E1;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 10px;
    font-size: 16px;
    padding: 0px;
}
QPushButton#AttachButton:hover, QPushButton#MicButton:hover, QPushButton#EmojiButton:hover {
    background-color: rgba(0, 240, 255, 0.15);
    border: 1px solid #00F0FF;
    color: #00F0FF;
}
QPushButton#AttachButton:pressed, QPushButton#MicButton:pressed, QPushButton#EmojiButton:pressed {
    background-color: rgba(0, 240, 255, 0.30);
    border: 1px solid #00F0FF;
}

/* Accent Indigo/Violet Gradient Send Button */
QPushButton#PrimaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366F1, stop:1 #4F46E5);
    color: #FFFFFF;
    border: 1px solid #818CF8;
    border-radius: 8px;
    font-weight: bold;
    font-size: 13px;
    padding: 6px 16px;
}
QPushButton#PrimaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #818CF8, stop:1 #6366F1);
    border-color: #A5B4FC;
    color: #FFFFFF;
}
QPushButton#PrimaryButton:pressed {
    background: #4338CA;
    border-color: #6366F1;
}
QPushButton#PrimaryButton:disabled {
    background: #1E2333;
    color: #6B7280;
    border-color: #2D3748;
}

/* Abort / Stop Button */
QPushButton#StopButton {
    background-color: #2D1120;
    color: #FF2E93;
    border: 1px solid #FF007F;
    font-weight: bold;
}
QPushButton#StopButton:hover {
    background-color: #3D142A;
    border-color: #FF47A3;
    color: #FFFFFF;
}
QPushButton#StopButton:pressed {
    background-color: #1E0B15;
}

/* Sidebar Mode Switcher Buttons */
QPushButton.ModeTab {
    background-color: transparent;
    color: #94A3B8;
    border: none;
    border-radius: 6px;
    padding: 8px 12px;
    text-align: left;
    font-weight: 600;
}
QPushButton.ModeTab:hover {
    background-color: #171E2D;
    color: #E2E8F0;
}
QPushButton.ModeTab[active="true"] {
    background-color: #1C2538;
    color: #00F0FF;
    border-left: 3px solid #00F0FF;
}

/* Dropdowns / ComboBoxes */
QComboBox {
    background-color: #151B27;
    color: #E2E8F0;
    border: 1px solid #28334A;
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 24px;
}
QComboBox:hover {
    border-color: #00F0FF;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left-width: 0px;
}
QComboBox QAbstractItemView {
    background-color: #151B27;
    color: #E2E8F0;
    border: 1px solid #28334A;
    selection-background-color: #1E273A;
    selection-color: #00F0FF;
    outline: none;
}

/* Scrollbars */
QScrollBar:vertical {
    border: none;
    background-color: #0A0D14;
    width: 8px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background-color: #202A3C;
    min-height: 20px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background-color: #00F0FF;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    border: none;
    background-color: #0A0D14;
    height: 8px;
    margin: 0px;
}
QScrollBar::handle:horizontal {
    background-color: #202A3C;
    min-width: 20px;
    border-radius: 4px;
}

/* Status Bar */
QStatusBar {
    background-color: #080A0F;
    border-top: 1px solid #171E2D;
    color: #7A8BA6;
    font-size: 12px;
    padding: 2px 6px;
}

/* Slider */
QSlider::groove:horizontal {
    border: none;
    height: 4px;
    background: #232C3E;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #00F0FF;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #E2E8F0;
    border: 1px solid #00F0FF;
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}
QSlider::handle:horizontal:hover {
    background: #00F0FF;
}

/* Tooltips */
QToolTip {
    background-color: #151B27;
    color: #00F0FF;
    border: 1px solid #28334A;
    padding: 4px 8px;
    border-radius: 4px;
}
"""
