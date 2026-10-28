with open('gods-eye-view-main/.env', 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()
    print("Total chars:", len(content))
    for i, line in enumerate(content.splitlines()):
        if '=' in line:
            k, v = line.split('=', 1)
            print(f"Line {i}: key='{k}' val_len={len(v)} is_empty={len(v.strip()) == 0}")
        else:
            print(f"Line {i}: {line}")
