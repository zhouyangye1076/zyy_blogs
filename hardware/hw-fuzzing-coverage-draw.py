import matplotlib.pyplot as plt
import matplotlib.patches as patches
from random import random

# 创建一个8x16的网格图形
fig, ax = plt.subplots(figsize=(12, 6))
ax.set_xlim(0, 16)
ax.set_ylim(0, 8)
ax.set_xticks(range(16))
ax.set_yticks(range(8))
ax.set_xticklabels(range(0,16,1))  # 下沿标记0-15
ax.set_yticklabels(range(0,8,1))   # 左侧标记0-7
ax.grid(True, color='black', linestyle='-', linewidth=1)

# 将整个网格填充为蓝色
for x in range(16):
    for y in range(8):
        rect = patches.Rectangle((x, y), 1, 1, facecolor='blue', alpha=0.3)
        ax.add_patch(rect)

# 将(2,2)-(5,8)的矩形填充为绿色
# 注意：matplotlib的y轴是反的，(2,2)实际是第2列第6行
green_rect = patches.Rectangle((2, 2), 6, 4, facecolor='green', alpha=0.5)
ax.add_patch(green_rect)

# 设置坐标轴标签
ax.set_xlabel('X Coordinate (0-15)')
ax.set_ylabel('Y Coordinate (0-7)')
ax.set_title('8x16 Grid with Colored Rectangle')

plt.tight_layout()
plt.savefig('img/coverage.png')

#################################################3


# 创建一个8x16的网格图形
ax.set_xticklabels(range(0,160,10))  # 下沿标记0-15
ax.set_yticklabels(range(0,80,10))   # 左侧标记0-7

# 设置坐标轴标签
ax.set_xlabel('X Coordinate (0-159)')
ax.set_ylabel('Y Coordinate (0-79)')
ax.set_title('80x160 Grid with Colored Rectangle')

plt.tight_layout()
plt.savefig('img/coverage2.png')

# for i in range(1280):
#     ax.plot(random()*16, random()*8, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)

# plt.savefig('img/coverage-random.png')

# for i in range(20, 80):
#     ax.plot(i/10, 2, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)
#     ax.plot(i/10, 6, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)

# for j in range(20, 60):
#     ax.plot(2, j/10, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)
#     ax.plot(8, j/10, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)

# plt.savefig('img/coverage-edge.png')

# for i in range(40):
#     ax.plot(random()*6+2, 2, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)
#     ax.plot(random()*6+2, 6, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)

# for j in range(40):
#     ax.plot(2, random()*4+2, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)
#     ax.plot(8, random()*4+2, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)

# plt.savefig('img/coverage-edge-random.png')

# for i in range(600):
#     ax.plot(random()*16, random()*8, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)

# for i in range(20, 80):
#     ax.plot(i/10, 2, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)
#     ax.plot(i/10, 6, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)

# for j in range(20, 60):
#     ax.plot(2, j/10, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)
#     ax.plot(8, j/10, 'ro', markersize=5)  # 使用网格中心坐标(3.5,4.5)

# plt.savefig('img/coverage-together.png')
