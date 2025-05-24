
import matplotlib.pyplot as plt
import numpy as np
import math

# 创建图形
plt.figure(figsize=(10, 6))

# 生成对数曲线数据 (y = log(x+1) 缩放至0-10范围)
x = np.linspace(0.1, 10, 100)  # 从0.1开始避免log(0)
y = np.log(x + 1) * (10 / np.log(11))  # 缩放至0-10范围
# print(y)

# 绘制曲线
plt.plot(x, y, 'r-', linewidth=2, label='y ~ log(x)')

# 生成对数曲线数据 (y = log(x+1) 缩放至0-10范围)
x = np.linspace(0.1, 10, 100)  # 从0.1开始避免log(0)
y = np.linspace(math.log(0.1 + 1) * (10 / math.log(11)), math.log(10 + 1) * (10 / math.log(11)), 100)  # 缩放至0-10范围

# 绘制曲线
plt.plot(x, y, 'b-', linewidth=2)

# 生成对数曲线数据 (y = log(x+1) 缩放至0-10范围)
x = np.linspace(7, 10, 10)  # 从0.1开始避免log(0)
y = np.linspace(math.log(7 + 1) * (10 / math.log(11)), math.log(10 + 1) * (10 / math.log(11)), 10)  # 缩放至0-10范围

# 绘制曲线
plt.plot(x, y, 'b-', linewidth=2)

# 添加图注和标签
plt.title('Logarithmic Curve (y ~ log(x))')
plt.xlabel('x')
plt.ylabel('y')
plt.legend()
plt.grid(True, linestyle='--', alpha=0.5)

plt.tight_layout()
plt.savefig('img/coverage_curve')