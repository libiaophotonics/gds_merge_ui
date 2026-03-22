from PIL import Image, ImageDraw
import os


def make_rounded_corners(input_path, output_path, corner_radius, crop_to_square=False):
    """
    给图片添加圆角

    :param input_path: 原图路径
    :param output_path: 输出图路径
    :param corner_radius: 圆角半径 (数值越大，角越圆)
    :param crop_to_square: 是否先裁剪成正方形 (默认 False)
    """
    try:
        # 1. 打开图片并转换为 RGBA 模式
        img = Image.open(input_path).convert("RGBA")
        width, height = img.size

        # 2. 如果需要，先裁剪成正方形
        if crop_to_square:
            # 取宽高最小值作为正方形边长
            size = min(width, height)
            left = (width - size) // 2
            top = (height - size) // 2
            right = left + size
            bottom = top + size
            img = img.crop((left, top, right, bottom))
            print(f"📐 已裁剪为正方形: {size}x{size}")

        # 3. 创建圆角遮罩
        mask = Image.new('L', img.size, 0)
        draw = ImageDraw.Draw(mask)

        # 4. 在遮罩上画白色圆角矩形
        # 白色(255)=保留，黑色(0)=透明
        draw.rounded_rectangle([(0, 0), img.size], radius=corner_radius, fill=255)

        # 5. 将遮罩应用到原图的 Alpha 通道
        img.putalpha(mask)

        # 6. 保存
        img.save(output_path)
        print(f"✅ 处理成功！圆角图片已保存至: {output_path}")

    except FileNotFoundError:
        print(f"❌ 错误：找不到文件 '{input_path}'")
    except Exception as e:
        print(f"❌ 发生未知错误: {e}")


# ==================== 运行配置 ====================

# 获取桌面路径 (兼容 Windows / Mac / Linux)
desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")

# 输入输出路径
input_file = os.path.join(desktop_path, "icon.png")
output_file = os.path.join(desktop_path, "icon_rounded.png")

# ---------- 圆角设置 ----------
# 圆角半径（数值越大越圆）
# iOS 图标风格：边长的 1/4.5
# 常见值：100-150 (基于 512x512 图片)
corner_radius = 110

# 是否裁剪成正方形（True=裁剪，False=保持原比例）
crop_to_square = False

# ---------- 运行 ----------
make_rounded_corners(input_file, output_file, corner_radius, crop_to_square)
