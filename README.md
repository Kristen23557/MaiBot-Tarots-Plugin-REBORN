# MaiBot_Tarots_Plugin_REBORN
塔罗牌插件 复活版1.0.0版本正式发布！

![QQ_1751117187452](https://github.com/user-attachments/assets/3eb9b721-aa41-4edb-86bf-fe9cf84fdcb9)

![QQ_1751117224351](https://github.com/user-attachments/assets/7b13be4b-4b66-48e1-9c0b-d07ff65dc7ea)

这是给MaiM-with-u项目开发的一个抽塔罗牌插件，具有模拟人类的调用方式和独特自定义风格的解牌回复。

现已适配最新版本的麦麦，如果出现问题可以去MaiCORE答疑群询问@卡秋KArabella或者等待修复。

**塔罗牌插件现在已实现牌组的解耦，现在可以自由编写添加自己的牌组了，详见[说明文档](https://github.com/A0000Xz/MaiBot-Tarots-Plugin/blob/main/help.md)（需要一定动手能力和技术能力）**

插件内不自带卡组，需要自行下载放入卡组文件夹，命名规则必须严格遵循示例卡组文件
牌组文件链接（度盘）：https://pan.baidu.com/s/1iPGeAtIUZggh0oxeKggBFQ?pwd=2357 提取码: 2357

原插件参考了https://github.com/FloatTech/ZeroBot-Plugin
此版本为对原插件 https://github.com/A0000Xz/MaiBot-Tarots-Plugin 进行向上兼容

卡牌图片来自于https://github.com/FloatTech/zbpdata

在此鸣谢[原插件开发者](https://github.com/A0000Xz/MaiBot-Tarots-Plugin)

完整的文件结构都包含在tarots_plugin这个大文件夹内，直接将这个大文件夹放入plugins中就能用。

使用时需指定抽牌方式和抽牌范围，目前已默认支持的有

牌阵："单张", "圣三角", "时间之流","四要素","五牌阵","吉普赛十字","马蹄","六芒星"

如果没有明确指定，默认抽"单张"。

范围："全部", "大阿卡纳", "小阿卡纳"

如果没有明确指定，默认抽"全部"。

—————上述说明是KArabella这个懒B照着原文件一个个改字眼的，以下为他自己想说的—————

在向上兼容这个插件的时候没有事先联系原作者和相关人员，若有不妥请联系（QQ 2785185004）谢谢
没了，因为他是懒B

复活版修订教程：
下面的添加示例牌组可以忽略了（中括号括出部分），现在本仓库自带一份本地牌组

从code下载整个分支，解压后的一个文件夹直接放入plugins文件夹内，[下载牌组文件，牌组文件内包含一个文件夹（tarots_jsons），解压整个放进插件文件夹内即可]
重要：如果你在使用时提升牌面获取失败，就去牌库文件夹找到对应的牌面把它的名字纠正过来（把牌库内对应牌面文件名称改成主程序提示的名称，正逆位及其括号不用改）（目前发现的命名问题字眼有【王后需改为皇后，隐者需改为隐士】），不需要重启bot就能生效

关于自定义牌组：复活版还没测试自定义牌组功能是否正常，不过你可以试试，和旧版本添加新牌组方法相同，但是因为仅限本地牌面（方便那些maibot连不上github的人）所以需要你提前准备好牌面文件并按照示例牌组里的命名方式命名文件
