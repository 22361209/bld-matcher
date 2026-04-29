# 群晖 NAS Docker 部署说明

## 1. 准备目录

在群晖 File Station 中创建目录：

```text
/docker/bld-matcher
```

把本项目所有文件放进这个目录。

确认这些目录存在：

```text
/docker/bld-matcher/data
/docker/bld-matcher/uploads
/docker/bld-matcher/outputs
```

如果没有，可以手动创建。`data` 目录最重要，里面的 `products.sqlite3` 是产品库、账号和日志。

## 2. 使用 Container Manager 启动

1. 打开群晖 `Container Manager`
2. 进入 `项目`
3. 点击 `新增`
4. 项目名称填写：`bld-matcher`
5. 路径选择：`/docker/bld-matcher`
6. 选择使用现有 `docker-compose.yml`
7. 构建并启动

## 3. 访问地址

启动成功后，在办公室电脑浏览器访问：

```text
http://群晖局域网IP:5055
```

例如：

```text
http://192.168.1.20:5055
```

## 4. 首次登录

管理员账号：

```text
登录名：007
密码：由 DEFAULT_ADMIN_PASSWORD 环境变量决定 (默认 change-me-on-first-login)
```

**首次启动前**请在 `docker-compose.yml` 的 `environment` 中设置 `DEFAULT_ADMIN_PASSWORD` 和强随机的 `SECRET_KEY`,登录后立即在 `账号管理` 中再次修改密码。

登录后可以在 `账号管理` 中新增其他用户、修改角色、重置密码。

## 5. 数据备份

重点备份：

```text
/docker/bld-matcher/data/products.sqlite3
```

建议定期备份整个目录：

```text
/docker/bld-matcher/data
```

`uploads` 是上传过的询价源文件，`outputs` 是生成的结果文件，也可以按需要备份。

## 6. 升级程序

以后更新程序时：

1. 停止 Container Manager 中的 `bld-matcher`
2. 替换项目代码
3. 不要删除 `data` 目录
4. 重新构建并启动项目

## 7. 安全建议

建议只在办公室内网使用。不要直接把 5055 端口开放到公网。

如果需要外网访问，建议使用 VPN，或配置群晖反向代理、HTTPS 和强密码策略。
