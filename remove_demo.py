import sys

def main():
    with open('frontend/reasoning.html', 'r', encoding='utf-8') as f:
        lines = f.readlines()

    out_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 1. Skip CSS
        if '/* ============================================' in line and i+1 < len(lines) and 'DEMO TRIGGER PANEL' in lines[i+1]:
            while i < len(lines) and '</style>' not in lines[i]:
                i += 1
            if len(out_lines) > 0 and out_lines[-1].strip() == '':
                out_lines.pop() # remove extra empty line before this block
            continue
            
        # 2. Skip HTML
        if '<!-- Demo Trigger Panel -->' in line:
            while i < len(lines) and '<!-- Pipeline visualization -->' not in lines[i]:
                i += 1
            if len(out_lines) > 0 and out_lines[-1].strip() == '':
                out_lines.pop()
            continue
            
        # 3. Skip JS
        if '// ============================================' in line and i+1 < len(lines) and 'DEMO TRIGGER PANEL' in lines[i+1]:
            while i < len(lines) and '}, 1500);' not in lines[i]:
                i += 1
            i += 1 # skip the }, 1500); line
            if len(out_lines) > 0 and out_lines[-1].strip() == '':
                out_lines.pop()
            continue
            
        out_lines.append(line)
        i += 1

    with open('frontend/reasoning.html', 'w', encoding='utf-8') as f:
        f.writelines(out_lines)

if __name__ == '__main__':
    main()
