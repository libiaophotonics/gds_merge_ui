from PIL import Image, ImageDraw
import os


def make_rounded_corners(input_path, output_path, radius):
    """
    给图片添加圆角
    :param input_path: 原图路径
    :param output_path: 输出图路径
    :param radius: 圆角半径 (数值越大，角越圆)
    """
    try:
        # 1. 打开图片并转换为 RGBA 模式（必须有 A 才能支持透明背景）
        img = Image.open(input_path).convert("RGBA")

        # 2. 创建一个与原图大小相同的全黑遮罩层（'L' 模式代表8位灰度图）
        mask = Image.new('L', img.size, 0)
        draw = ImageDraw.Draw(mask)

        # 3. 在遮罩层上画一个白色的圆角矩形
        # 白色 (255) 代表保留原图内容，黑色 (0) 代表变成透明
        draw.rounded_rectangle([(0, 0), img.size], radius=radius, fill=255)

        # 4. 把原图的 Alpha（透明）通道替换成我们画的这个圆角遮罩
        img.putalpha(mask)

        # 5. 保存处理后的图片
        img.save(output_path)
        print(f"✅ 处理成功！圆角图片已保存至: {output_path}")

    except FileNotFoundError:
        print(f"❌ 错误：在桌面上找不到文件 '{input_path}'，请检查文件名是否正确。")
    except Exception as e:
        print(f"❌ 发生未知错误: {e}")


# --- 运行配置 ---

# 获取当前用户的桌面路径 (兼容 Windows / Mac / Linux)
desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")

input_file = os.path.join(desktop_path, "icon.png")
output_file = os.path.join(desktop_path, "icon_rounded.png")

# 设置圆角半径
# 提示：如果是 iOS 风格的图标，圆角半径通常大约是图片边长的 1/4.5 (约 22%)
# 假设你的图片是 512x512，可以把 radius 设置为 110 左右。你可以根据实际需求微调。
corner_radius = 150

make_rounded_corners(input_file, output_file, radius=corner_radius)