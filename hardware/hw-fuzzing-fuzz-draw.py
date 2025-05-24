import matplotlib.pyplot as plt
import matplotlib.patches as patches
from random import random, choice

def fuzz(map_array, dot_list, max_time):
    def mutate_small(point):
        point = [point[0] + (random() - 0.5)*0.3, point[1] + (random() - 0.5)*0.3]
        if point[0] < 0:
            point[0] = 0
        elif point[0] >= 16:
            point[0] = 15.9
        if point[1] < 0:
            point[1] = 0
        elif point[1] >= 8:
            point[1] = 7.9
        return point
    
    def mutate_large(point):
        purple_kind = set((i, j) for i in range(16) for j in range(8))
        green_kind = set([(i, j) for i in range(2, 6) for j in range(2, 6)])
        orangle_kind = set([(i, j) for i in range(8, 12) for j in range(1, 5)])
        gray_kind = set([(i, j) for i in range(12, 16) for j in range(3, 7)])
        purple_kind = purple_kind - gray_kind - green_kind - orangle_kind
        match int(random()*4):
            case 0:
                point = choice(list(purple_kind))
            case 1:
                point = choice(list(green_kind))
            case 2:
                point = choice(list(orangle_kind))
            case _:
                point = choice(list(gray_kind))
        point = (point[0] + random(), point[1] + random())
        return point
    
    last_color = 'blue'
    mutate_time = 0
    point = [0, 0]
    point = mutate_large(point)
    while len(dot_list) < max_time:
        # print(point)
        color = map_array[int(point[1])][int(point[0])]
        if color == last_color:
            if mutate_time < 20:
                point = mutate_small(point)
                mutate_time += 1
            else:
                point = mutate_large(point)
                mutate_time = 0
        else:
            point = mutate_small(point)
            mutate_time = 0
        dot_list.append(point)
        last_color = color
    return dot_list

def draw_coverage(map_array, dot_list, file_name):
    # 创建一个8x16的网格图形
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 8)
    ax.set_xticks(range(16))
    ax.set_yticks(range(8))
    ax.set_xticklabels(range(0,16,1))  # 下沿标记0-15
    ax.set_yticklabels(range(0,8,1))   # 左侧标记0-7
    ax.grid(True, color='black', linestyle='-', linewidth=1)

    for i in range(8):
        for j in range(16):
            rect = patches.Rectangle((j, i), 1, 1, facecolor=map_array[i][j][0], alpha=map_array[i][j][1])
            ax.add_patch(rect)

    for dot in dot_list:
        ax.plot(dot[0], dot[1], 'ro', markersize=2)

    # 设置坐标轴标签
    ax.set_xlabel('X Coordinate (0-15)')
    ax.set_ylabel('Y Coordinate (0-7)')
    ax.set_title('8x16 Grid with Colored Rectangle')

    plt.tight_layout()
    plt.savefig(file_name)

def set_color(map_array, x, y, width, high, color):
    for i in range(y, y+high//2):
        for j in range(x, x+width//2):
            map_array[i][j] = (color, 0.1)
    for i in range(y+high//2, y+high):
        for j in range(x, x+width//2):
            map_array[i][j] = (color, 0.3)
    for i in range(y, y+high//2):
        for j in range(x+width//2, x+width):
            map_array[i][j] = (color, 0.5)
    for i in range(y+high//2, y+high):
        for j in range(x+width//2, x+width):
            map_array[i][j] = (color, 0.7)
    
map_array = [[('blue', 0.3) for i in range(16)] for j in range(8)]
set_color(map_array, 2, 2, 4, 4, 'green')
set_color(map_array, 8, 1, 4, 4, 'orange')
set_color(map_array, 12, 3, 4, 4, 'gray')

dot_list = fuzz(map_array, [], 100)
# draw_coverage(map_array, dot_list, 'img/coverage-kind_fuzz.png')
dot_list = fuzz(map_array, dot_list, 1000)
# draw_coverage(map_array, dot_list, 'img/coverage-kind_fuzz-1.png')
dot_list = fuzz(map_array, dot_list, 10000)
draw_coverage(map_array, dot_list, 'img/coverage-kind_fuzz-2.png')

