"""礼品店（额外扩展）：「礼品店」看编号和礼物，送礼时可以直接写编号。管理（上架、导出、导入）在后台「礼品店」页。"""
from nonebot import on_command

from changri.core import gifts

shop = on_command("礼品店", priority=10, block=True)


@shop.handle()
async def _():
    its = gifts.items()
    if not its:
        await shop.finish("礼品店还没有上架礼物，送礼可以随便写礼物名。")
    tip = "只能送这里的礼物，要写编号。" if gifts.only_preset() else "也可以送别的，随便写。"
    await shop.finish("🎁 礼品店：\n" + "\n".join(f"{x['id']}. {x['name']}" + (f"（{x['note']}）" if x["note"] else "") for x in its)
                      + f"\n\n送礼 对象 编号 [留言]，例：送礼 乙 {its[0]['id']}。{tip}")
