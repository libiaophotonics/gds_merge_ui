import os
import sys
import subprocess


def main():
    # ⚠️ 确保你刚才写的带有 UI 的主程序文件名为 "merge_gds_ui.py"
    target_script = "merge_gds_ui.py"
    app_name = "WaferForge_GDS_Assembler"  # 更新了更酷的软件名
    icon_png = "icon.png"
    icon_ico = "icon.ico"

    # 1. 检查主程序文件是否存在
    if not os.path.exists(target_script):
        print(f"❌ 错误: 在当前目录下找不到 '{target_script}'！")
        print("请确保打包脚本与你的 UI 脚本在同一个文件夹内。")
        sys.exit(1)

    # 2. 自动检查并安装必备的打包库
    try:
        import PyInstaller
        print("✅ 检测到已安装 PyInstaller。")
    except ImportError:
        print("⚠️ 未检测到 PyInstaller，正在为你自动安装...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
            print("✅ PyInstaller 安装完成！")
        except Exception as e:
            print(f"❌ 安装 PyInstaller 失败: {e}")
            sys.exit(1)

    try:
        from PIL import Image
        print("✅ 检测到已安装 Pillow (图像处理库)。")
    except ImportError:
        print("⚠️ 未检测到 Pillow，正在为你自动安装以转换图标格式...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
            print("✅ Pillow 安装完成！")
            from PIL import Image
        except Exception as e:
            print(f"❌ 安装 Pillow 失败: {e}")
            sys.exit(1)

    import PyInstaller.__main__

    # 3. 自动转换 PNG 为 ICO
    use_icon = False
    if os.path.exists(icon_png):
        print(f"🔄 正在将 {icon_png} 转换为 Windows 支持的 {icon_ico} 格式...")
        try:
            img = Image.open(icon_png)
            # 生成包含多种尺寸的 ICO 文件，确保在不同分辨率下都清晰
            img.save(icon_ico, format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (32, 32)])
            use_icon = True
            print("✅ 图标格式转换成功！")
        except Exception as e:
            print(f"⚠️ 图标转换失败，将忽略图标设置: {e}")
    elif os.path.exists(icon_ico):
        use_icon = True
        print(f"✅ 发现已存在的 {icon_ico}。")
    else:
        print(f"ℹ️ 未在目录下找到 {icon_png} 或 {icon_ico}，将使用系统默认图标。")

    # 4. 配置打包参数
    print(f"\n🚀 开始将 {target_script} 打包为独立的 .exe 程序...")

    pyinstaller_args = [
        target_script,
        f'--name={app_name}',
        '--onefile',
        '--windowed',
        '--clean',
        '--hidden-import=klayout.db',
        '--collect-all=klayout',
        '--hidden-import=pyqtgraph',
        '--hidden-import=PyQt5',

        # 🚀 【修复核心】强制排除其他干扰的 Qt 库和不需要的 OpenGL 模块
        '--exclude-module=PySide2',
        '--exclude-module=PySide6',
        '--exclude-module=PyQt6',
        '--exclude-module=OpenGL',

        '--log-level=WARN',
    ]

    # 如果图标存在且可用，把图标参数加进打包配置里
    if use_icon:
        pyinstaller_args.append(f'--icon={icon_ico}')

    # 5. 执行打包
    try:
        PyInstaller.__main__.run(pyinstaller_args)
        print("\n" + "=" * 50)
        print(f"🎉 打包大功告成！")
        print(f"请在当前目录的 'dist' 文件夹中寻找你的程序: {app_name}.exe")
        print("=" * 50)
    except Exception as e:
        print(f"\n❌ 打包过程中发生错误: {e}")


if __name__ == "__main__":
    main()